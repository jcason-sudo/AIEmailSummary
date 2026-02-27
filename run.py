#!/usr/bin/env python3
"""
Email RAG Application - Main Entry Point

Usage:
    python run.py                    # Start the web server
    python run.py --ingest           # Run email ingestion only
    python run.py --ingest --serve   # Ingest then start server
"""

import sys
import argparse
import logging
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

import config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run_ingestion(args):
    """Run email ingestion."""
    from ingestion import run_ingestion as do_ingestion
    
    logger.info("Starting email ingestion...")
    
    pst_paths = [Path(p.strip()) for p in args.pst.split(',') if p.strip()] if args.pst else None
    
    stats = do_ingestion(
        pst_paths=pst_paths,
        include_outlook=not args.no_outlook,
        days_back=args.days
    )
    
    logger.info(f"Ingestion complete: {stats}")
    return stats


def run_server(host=None, port=None):
    """Run the Flask web server."""
    from app import app
    
    h = host or config.HOST
    p = port or config.PORT
    
    logger.info(f"Starting server at http://{h}:{p}")
    app.run(host=h, port=p, debug=True, threaded=True)


def main():
    parser = argparse.ArgumentParser(
        description='Email RAG Application - Local email intelligence'
    )
    
    parser.add_argument(
        '--ingest', 
        action='store_true',
        help='Run email ingestion'
    )
    parser.add_argument(
        '--serve', 
        action='store_true',
        help='Start the web server'
    )
    parser.add_argument(
        '--pst',
        type=str,
        default='',
        help='Comma-separated PST file paths'
    )
    parser.add_argument(
        '--no-outlook',
        action='store_true',
        help='Skip Outlook connection'
    )
    parser.add_argument(
        '--days',
        type=int,
        default=config.EMAIL_LOOKBACK_DAYS,
        help='Days of email history to ingest'
    )
    parser.add_argument(
        '--host',
        type=str,
        default=config.HOST,
        help='Server host'
    )
    parser.add_argument(
        '--port',
        type=int,
        default=config.PORT,
        help='Server port'
    )
    
    args = parser.parse_args()
    
    # Determine action
    if args.ingest:
        run_ingestion(args)
        if args.serve:
            run_server(args.host, args.port)
    elif args.serve or len(sys.argv) == 1:
        # Default: just start server
        run_server(args.host, args.port)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
