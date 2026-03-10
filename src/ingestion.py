"""
Email ingestion from PST files, Outlook, and IMAP (Gmail, Yahoo, etc.).

Supports incremental sync with dedup, change detection, and priority indexing:
- Sync watermarks per account track last sync time for incremental resume
- Dedup by Message-ID hash + normalized body hash
- Change detection for email status (read/replied/flagged)
- Priority: last 14 days first, then backfill older messages (first run only)
- Retention cleanup removes old emails after ingestion
"""

import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional

from vector_store import get_vector_store
from pst_parser import PSTParser
from outlook_connection import OutlookConnection, OUTLOOK_AVAILABLE
from imap_connection import IMAPConnection, parse_imap_accounts
from sync_state import get_sync_state, EmailCheckResult
import config

logger = logging.getLogger(__name__)

PRIORITY_DAYS = 14  # Index last N days first for freshness


def _dedup_and_batch(emails_iter, store, sync, account_id: str, label: str,
                     batch_size: int = 100) -> dict:
    """Process an email iterator with dedup, change detection, batching, and sync tracking.

    Returns dict with total_added, total_found, total_skipped, total_updated.
    """
    log_id = sync.start_sync_log(account_id)
    total_added = 0
    total_found = 0
    total_skipped = 0
    total_updated = 0
    batch = []
    last_date = None

    try:
        for em in emails_iter:
            total_found += 1

            # Check: new, duplicate, or changed status
            result = sync.check_email(
                em.message_id, em.body_text,
                is_read=em.is_read, is_replied=em.is_replied, is_flagged=em.is_flagged,
            )

            if result == EmailCheckResult.DUPLICATE:
                total_skipped += 1
                continue

            if result == EmailCheckResult.CHANGED:
                # Update metadata in ChromaDB without re-embedding
                metadata_updates = {
                    'is_read': em.is_read,
                    'is_replied': em.is_replied,
                    'is_flagged': em.is_flagged,
                }
                updated = store.update_email_metadata(em.unique_id, metadata_updates)
                if updated:
                    sync.update_seen_status(
                        em.message_id,
                        is_read=em.is_read,
                        is_replied=em.is_replied,
                        is_flagged=em.is_flagged,
                    )
                    total_updated += 1
                continue

            # NEW email — add to batch
            batch.append(em)

            # Track latest message date for watermark
            if em.date:
                date_str = em.date.isoformat()
                if last_date is None or date_str > last_date:
                    last_date = date_str

            if len(batch) >= batch_size:
                added = store.add_emails(batch)
                total_added += added
                # Mark all as seen
                for e in batch:
                    sync.mark_seen(
                        e.message_id, e.unique_id, e.body_text,
                        is_read=e.is_read, is_replied=e.is_replied, is_flagged=e.is_flagged,
                    )
                logger.info(f"Batch ({label}): {added} added, {total_skipped} skipped, {total_updated} updated (total: {total_added})")
                batch = []

        # Final batch
        if batch:
            added = store.add_emails(batch)
            total_added += added
            for e in batch:
                sync.mark_seen(
                    e.message_id, e.unique_id, e.body_text,
                    is_read=e.is_read, is_replied=e.is_replied, is_flagged=e.is_flagged,
                )

        sync.complete_sync_log(log_id, total_found, total_added, total_skipped)
        sync.update_watermark(
            account_id, label,
            last_message_date=last_date,
            messages_synced=total_added,
            status='idle',
        )

    except Exception as exc:
        sync.complete_sync_log(log_id, total_found, total_added, total_skipped,
                               status='error', error=str(exc))
        raise

    return {
        'total_added': total_added,
        'total_found': total_found,
        'total_skipped': total_skipped,
        'total_updated': total_updated,
    }


def ingest_pst(pst_path: Path, since: Optional[datetime] = None,
               full_sync: bool = False) -> dict:
    """Ingest emails from a PST file with dedup and sync tracking."""
    store = get_vector_store()
    sync = get_sync_state()

    if not pst_path.exists():
        logger.error(f"PST file not found: {pst_path}")
        return {'total_added': 0, 'total_found': 0, 'total_skipped': 0, 'total_updated': 0}

    account_id = f"pst_{pst_path.name}"
    label = f"PST:{pst_path.name}"
    logger.info(f"Ingesting PST: {pst_path}")

    # Incremental: use watermark if available
    if not full_sync and since:
        since = sync.get_incremental_since(account_id, since)

    sync.update_watermark(account_id, label, status='syncing')

    def email_gen():
        with PSTParser(pst_path) as parser:
            yield from parser.get_emails(since=since)

    result = _dedup_and_batch(email_gen(), store, sync, account_id, label)
    logger.info(
        f"PST ingestion complete: {result['total_added']} added, "
        f"{result['total_skipped']} duplicates, {result['total_updated']} updated from {pst_path}"
    )
    return result


def ingest_outlook(since: Optional[datetime] = None,
                   full_sync: bool = False) -> dict:
    """Ingest from live Outlook connection with dedup and sync tracking."""
    empty = {'total_added': 0, 'total_found': 0, 'total_skipped': 0, 'total_updated': 0}
    if not OUTLOOK_AVAILABLE:
        logger.warning("Outlook not available (Windows only)")
        return empty

    store = get_vector_store()
    sync = get_sync_state()
    account_id = "outlook_local"
    label = "Outlook"

    # Incremental: use watermark if available
    if not full_sync and since:
        since = sync.get_incremental_since(account_id, since)

    logger.info("Ingesting from Outlook...")
    sync.update_watermark(account_id, label, status='syncing')

    def email_gen():
        with OutlookConnection() as conn:
            yield from conn.get_emails(
                folders=config.OUTLOOK_FOLDERS,
                since=since,
            )

    result = _dedup_and_batch(email_gen(), store, sync, account_id, label)
    logger.info(
        f"Outlook ingestion complete: {result['total_added']} added, "
        f"{result['total_skipped']} duplicates, {result['total_updated']} updated"
    )
    return result


def ingest_imap(since: Optional[datetime] = None,
                full_sync: bool = False) -> dict:
    """Ingest from configured IMAP accounts with dedup and sync tracking."""
    accounts = parse_imap_accounts(config.IMAP_ACCOUNTS)
    if not accounts:
        logger.info("No IMAP accounts configured")
        return {}

    store = get_vector_store()
    sync = get_sync_state()
    results = {}

    for acct in accounts:
        label = acct.get('provider', acct['host'])
        account_id = f"imap_{acct['username']}"
        logger.info(f"Ingesting from IMAP: {label} ({acct['username']})")

        # Incremental: use watermark if available
        effective_since = since
        if not full_sync and since:
            effective_since = sync.get_incremental_since(account_id, since)

        sync.update_watermark(account_id, label, status='syncing')

        conn = IMAPConnection(
            host=acct['host'],
            port=acct['port'],
            username=acct['username'],
            password=acct['password'],
            provider=acct.get('provider', 'default'),
            label=label,
        )

        try:
            def email_gen(c=conn, s=effective_since):
                with c:
                    yield from c.get_emails(since=s)

            result = _dedup_and_batch(email_gen(), store, sync, account_id, label)
            logger.info(
                f"IMAP ingestion complete ({label}): {result['total_added']} added, "
                f"{result['total_skipped']} duplicates, {result['total_updated']} updated"
            )
            results[f"imap_{label}"] = result

        except Exception as e:
            logger.error(f"IMAP ingestion error for {label}: {e}")
            results[f"imap_{label}"] = {
                'total_added': 0, 'total_found': 0, 'total_skipped': 0, 'total_updated': 0
            }

    return results


def run_ingestion(
    pst_paths: Optional[List[Path]] = None,
    include_outlook: bool = True,
    include_imap: bool = True,
    days_back: int = 365,
    full_sync: bool = False,
    retention_days: Optional[int] = None,
) -> dict:
    """Run full ingestion with incremental sync and retention cleanup.

    On first run (no watermarks): uses priority indexing (last 14 days first,
    then backfill). On subsequent runs: uses watermark-based incremental sync
    to only fetch new emails since last sync.

    Args:
        full_sync: Force full re-scan, ignoring watermarks.
        retention_days: Delete emails older than this. Defaults to EMAIL_RETENTION_DAYS.
    """
    now = datetime.now()
    sync = get_sync_state()
    results = {
        "pst_emails": 0,
        "outlook_emails": 0,
        "updated_emails": 0,
    }

    # Check if this is an incremental run (watermarks exist for any account)
    has_watermarks = bool(sync.get_all_watermarks()) and not full_sync

    if has_watermarks:
        # --- Incremental mode: single pass, watermark determines since ---
        logger.info("Incremental sync: using watermarks to fetch only new emails")
        fallback_since = now - timedelta(days=days_back)

        if pst_paths:
            for path in pst_paths:
                path = Path(path)
                if path.is_file():
                    r = ingest_pst(path, fallback_since, full_sync=False)
                    results["pst_emails"] += r['total_added']
                    results["updated_emails"] += r['total_updated']
                elif path.is_dir():
                    for pst in path.glob("**/*.pst"):
                        r = ingest_pst(pst, fallback_since, full_sync=False)
                        results["pst_emails"] += r['total_added']
                        results["updated_emails"] += r['total_updated']

        if include_outlook:
            r = ingest_outlook(fallback_since, full_sync=False)
            results["outlook_emails"] = r['total_added']
            results["updated_emails"] += r['total_updated']

        if include_imap:
            imap_results = ingest_imap(fallback_since, full_sync=False)
            for k, r in imap_results.items():
                results[k] = r['total_added']
                results["updated_emails"] += r['total_updated']

    else:
        # --- First run or full sync: two-phase priority + backfill ---
        # Phase 1: Priority window (last N days)
        priority_since = now - timedelta(days=PRIORITY_DAYS)
        logger.info(f"{'Full sync' if full_sync else 'First run'}: "
                     f"indexing last {PRIORITY_DAYS} days first")

        if pst_paths:
            for path in pst_paths:
                path = Path(path)
                if path.is_file():
                    r = ingest_pst(path, priority_since, full_sync=True)
                    results["pst_emails"] += r['total_added']
                elif path.is_dir():
                    for pst in path.glob("**/*.pst"):
                        r = ingest_pst(pst, priority_since, full_sync=True)
                        results["pst_emails"] += r['total_added']

        if include_outlook:
            r = ingest_outlook(priority_since, full_sync=True)
            results["outlook_emails"] = r['total_added']

        if include_imap:
            imap_results = ingest_imap(priority_since, full_sync=True)
            for k, r in imap_results.items():
                results[k] = r['total_added']

        # Phase 2: Backfill (older messages)
        backfill_since = now - timedelta(days=days_back)
        if days_back > PRIORITY_DAYS:
            logger.info(f"Backfill pass: indexing {days_back} days back (dedup handles overlap)")

            if pst_paths:
                for path in pst_paths:
                    path = Path(path)
                    if path.is_file():
                        r = ingest_pst(path, backfill_since, full_sync=True)
                        results["pst_emails"] += r['total_added']
                    elif path.is_dir():
                        for pst in path.glob("**/*.pst"):
                            r = ingest_pst(pst, backfill_since, full_sync=True)
                            results["pst_emails"] += r['total_added']

            if include_outlook:
                r = ingest_outlook(backfill_since, full_sync=True)
                results["outlook_emails"] += r['total_added']

            if include_imap:
                imap_backfill = ingest_imap(backfill_since, full_sync=True)
                for k, r in imap_backfill.items():
                    results[k] = results.get(k, 0) + r['total_added']

    # --- Post-ingestion: summaries, indexes, cleanup ---
    store = get_vector_store()
    total_in_db = store._collection.count()
    logger.info(f"Total chunks in database: {total_in_db}")

    # Generate thread summary chunks
    try:
        summaries = store.add_thread_summaries()
        results['thread_summaries'] = summaries
        logger.info(f"Generated {summaries} thread summary chunks")
    except Exception as e:
        logger.warning(f"Failed to generate thread summaries: {e}")

    # Rebuild BM25 index with new documents
    try:
        from bm25_index import get_bm25_index
        bm25 = get_bm25_index()
        bm25.build_from_chromadb(store._collection)
        logger.info(f"BM25 index rebuilt with {bm25.size} documents")
    except Exception as e:
        logger.warning(f"Failed to rebuild BM25 index: {e}")

    # Retention cleanup
    ret_days = retention_days if retention_days is not None else config.EMAIL_RETENTION_DAYS
    try:
        deleted_chunks = store.cleanup_old_emails(ret_days)
        deleted_seen = sync.cleanup_old_seen(ret_days)
        results['retention_deleted_chunks'] = deleted_chunks
        results['retention_deleted_seen'] = deleted_seen
    except Exception as e:
        logger.warning(f"Retention cleanup failed: {e}")

    # Sync stats
    results['sync_stats'] = sync.get_stats()

    return results
