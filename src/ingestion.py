"""
Email ingestion from PST files and Outlook.
"""

import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional

from vector_store import get_vector_store
from pst_parser import PSTParser
from outlook_connection import OutlookConnection, OUTLOOK_AVAILABLE
import config

logger = logging.getLogger(__name__)


def ingest_pst(pst_path: Path, since: Optional[datetime] = None) -> int:
    """Ingest emails from a PST file."""
    store = get_vector_store()
    
    if not pst_path.exists():
        logger.error(f"PST file not found: {pst_path}")
        return 0
    
    logger.info(f"Ingesting PST: {pst_path}")
    
    total_added = 0
    emails = []
    with PSTParser(pst_path) as parser:
        for email in parser.get_emails(since=since):
            emails.append(email)
            
            if len(emails) >= 100:
                added = store.add_emails(emails)
                total_added += added
                logger.info(f"Batch complete: {added} emails added (total: {total_added})")
                emails = []
    
    if emails:
        added = store.add_emails(emails)
        total_added += added
        
    logger.info(f"PST ingestion complete: {total_added} emails from {pst_path}")
    return total_added


def ingest_outlook(since: Optional[datetime] = None) -> int:
    """Ingest from live Outlook connection."""
    if not OUTLOOK_AVAILABLE:
        logger.warning("Outlook not available (Windows only)")
        return 0
        
    store = get_vector_store()
    
    logger.info("Ingesting from Outlook...")
    
    total_added = 0
    emails = []
    with OutlookConnection() as conn:
        for email in conn.get_emails(
            folders=config.OUTLOOK_FOLDERS,
            since=since
        ):
            emails.append(email)
            
            if len(emails) >= 100:
                added = store.add_emails(emails)
                total_added += added
                logger.info(f"Batch complete: {added} emails added (total: {total_added})")
                emails = []
    
    if emails:
        added = store.add_emails(emails)
        total_added += added
        
    logger.info(f"Outlook ingestion complete: {total_added} emails")
    return total_added


def run_ingestion(
    pst_paths: Optional[List[Path]] = None,
    include_outlook: bool = True,
    days_back: int = 365
) -> dict:
    """Run full ingestion."""
    
    since = datetime.now() - timedelta(days=days_back)
    logger.info(f"Ingesting emails since {since.strftime('%Y-%m-%d')}")
    
    results = {
        "pst_emails": 0,
        "outlook_emails": 0
    }
    
    # PST files
    if pst_paths:
        for path in pst_paths:
            path = Path(path)
            if path.is_file():
                results["pst_emails"] += ingest_pst(path, since)
            elif path.is_dir():
                for pst in path.glob("**/*.pst"):
                    results["pst_emails"] += ingest_pst(pst, since)
    
    # Outlook
    if include_outlook:
        results["outlook_emails"] = ingest_outlook(since)
    
    # Get final count from store
    store = get_vector_store()
    total_in_db = store._collection.count()
    logger.info(f"Total emails in database: {total_in_db}")
    
    return results
