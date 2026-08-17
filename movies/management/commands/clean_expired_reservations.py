from django.core.management.base import BaseCommand
from movies.models import SeatReservation
from django.utils import timezone

class Command(BaseCommand):
    help = 'Releases expired seat reservations'

    def handle(self, *args, **kwargs):
        expired = SeatReservation.objects.filter(expires_at__lt=timezone.now())
        count = expired.count()
        expired.delete()
        self.stdout.write(self.style.SUCCESS(f'Successfully deleted {count} expired reservations.'))
