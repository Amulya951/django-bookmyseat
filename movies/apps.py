import os
import sys
import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)

# Commands that must not spin up a scheduler thread.
NO_SCHEDULER_COMMANDS = {
    'makemigrations', 'migrate', 'collectstatic', 'shell', 'test',
    'createsuperuser', 'check', 'showmigrations', 'clean_expired_reservations',
}


class MoviesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'movies'

    def ready(self):
        if not self._should_start_scheduler():
            return

        from apscheduler.schedulers.background import BackgroundScheduler
        from django.utils import timezone

        def release_expired_reservations():
            from movies.models import SeatReservation
            expired = SeatReservation.objects.filter(expires_at__lt=timezone.now())
            count = expired.count()
            if count:
                expired.delete()
                logger.info("Released %s expired seat reservation(s)", count)

        scheduler = BackgroundScheduler()
        scheduler.add_job(
            release_expired_reservations,
            'interval',
            minutes=1,
            id='release_expired_reservations',
            replace_existing=True,
            max_instances=1,
        )
        scheduler.start()
        logger.info("Seat reservation scheduler started")

    @staticmethod
    def _should_start_scheduler():
        # Management commands other than runserver have no use for it.
        if len(sys.argv) > 1 and sys.argv[1] in NO_SCHEDULER_COMMANDS:
            return False
        # runserver's autoreloader imports the app twice; only the reloaded
        # child process has RUN_MAIN set, so this avoids two schedulers.
        # With --noreload there is no child, and RUN_MAIN is never set, so
        # that case has to be allowed through explicitly.
        if 'runserver' in sys.argv and '--noreload' not in sys.argv:
            if os.environ.get('RUN_MAIN') != 'true':
                return False
        return True
