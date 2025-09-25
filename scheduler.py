#!/usr/bin/env python3
"""
Real Scheduler module for Bol.com Product Tracker
Handles daily automatic checks at 9:00 AM Dutch time
"""

import threading
import time
from datetime import datetime, timedelta
import pytz
from product_tracker import ProductTracker
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ProductTrackerScheduler:
    def __init__(self):
        self.tracker = ProductTracker()
        self.running = False
        self.scheduler_thread = None
        self.dutch_tz = pytz.timezone('Europe/Amsterdam')
        
    def start_scheduler(self):
        """Start the daily scheduler"""
        if self.running:
            logger.info("Scheduler is already running")
            return
            
        self.running = True
        self.scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self.scheduler_thread.start()
        logger.info("Daily scheduler started - will check products at 9:00 AM Dutch time")
        
    def stop_scheduler(self):
        """Stop the daily scheduler"""
        self.running = False
        if self.scheduler_thread:
            self.scheduler_thread.join(timeout=5)
        logger.info("Daily scheduler stopped")
        
    def _scheduler_loop(self):
        """Main scheduler loop that runs continuously"""
        while self.running:
            try:
                # Calculate next 9:00 AM Dutch time
                next_run = self._get_next_run_time()
                now_dutch = datetime.now(self.dutch_tz)
                
                # Calculate seconds until next run
                seconds_until_run = (next_run - now_dutch).total_seconds()
                
                logger.info(f"Next scheduled check: {next_run.strftime('%Y-%m-%d %H:%M:%S')} Dutch time")
                logger.info(f"Time until next check: {seconds_until_run/3600:.1f} hours")
                
                # Sleep until next run time
                if seconds_until_run > 0:
                    time.sleep(seconds_until_run)
                
                if self.running:  # Check if we should still run
                    self._run_daily_checks()
                    
            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}")
                # Sleep for 1 hour before retrying
                time.sleep(3600)
                
    def _get_next_run_time(self):
        """Calculate the next 9:00 AM Dutch time"""
        now_dutch = datetime.now(self.dutch_tz)
        
        # Today's 9:00 AM
        today_9am = now_dutch.replace(hour=9, minute=0, second=0, microsecond=0)
        
        # If it's already past 9:00 AM today, schedule for tomorrow
        if now_dutch >= today_9am:
            tomorrow = now_dutch + timedelta(days=1)
            next_run = tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)
        else:
            next_run = today_9am
            
        return next_run
        
    def _run_daily_checks(self):
        """Run the daily product checks"""
        logger.info("Starting daily product checks...")
        
        try:
            results = self.tracker.run_scheduled_checks()
            
            # Log results
            successful = sum(1 for r in results if "error" not in r and r.get("status") != "deactivated_expired")
            errors = sum(1 for r in results if "error" in r)
            deactivated = sum(1 for r in results if r.get("status") == "deactivated_expired")
            
            logger.info(f"Daily checks completed: {successful} successful, {errors} errors, {deactivated} deactivated")
            
            # Log individual results for debugging
            for result in results:
                if "error" in result:
                    logger.error(f"Error checking product {result.get('id', 'unknown')}: {result['error']}")
                elif result.get("status") == "deactivated_expired":
                    logger.info(f"Product {result.get('id', 'unknown')} deactivated (expired)")
                else:
                    position = result.get("position", "Not found")
                    logger.info(f"Product {result.get('id', 'unknown')} - Position: {position}")
                    
        except Exception as e:
            logger.error(f"Error during daily checks: {e}")
            
    def get_next_run_time(self):
        """Get the next scheduled run time as a string"""
        if not self.running:
            return "Scheduler not running"
            
        next_run = self._get_next_run_time()
        return next_run.strftime('%Y-%m-%d %H:%M:%S %Z')
        
    def get_scheduler_status(self):
        """Get current scheduler status"""
        return {
            "running": self.running,
            "next_run": self.get_next_run_time(),
            "dutch_time_now": datetime.now(self.dutch_tz).strftime('%Y-%m-%d %H:%M:%S %Z')
        }
        
    def run_checks_now(self):
        """Manually trigger checks (for testing or immediate execution)"""
        logger.info("Manual check triggered")
        self._run_daily_checks()

# Global scheduler instance
scheduler = ProductTrackerScheduler()
