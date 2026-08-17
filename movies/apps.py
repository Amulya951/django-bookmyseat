from django.apps import AppConfig


class MoviesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'movies'
    def ready(self):
        from apscheduler.schedulers.background import BackgroundScheduler
        from django.utils import timezone

        def release_expired_reservations():
            from movies.models import SeatReservation
            expired = SeatReservation.objects.filter(expires_at__lt=timezone.now())
            count = expired.count()
            if count > 0:
                expired.delete()

        scheduler = BackgroundScheduler()
        scheduler.add_job(release_expired_reservations, 'interval', minutes=1)
        scheduler.start()