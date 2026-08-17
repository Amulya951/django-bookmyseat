"""Tests covering the security and concurrency guarantees claimed in the report.

Run with:  python manage.py test movies
"""
import hashlib
import hmac
import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Movie, Theater, Seat, Booking, SeatReservation, Payment
from .utils import youtube_embed_url
from .views import fulfil_payment


class TrailerUrlValidationTests(TestCase):
    """Task 3 — only YouTube hosts may be embedded."""

    def test_accepts_the_standard_youtube_forms(self):
        for url in [
            'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
            'https://youtu.be/dQw4w9WgXcQ',
            'https://www.youtube.com/shorts/dQw4w9WgXcQ',
            'https://m.youtube.com/watch?v=dQw4w9WgXcQ',
        ]:
            self.assertEqual(
                youtube_embed_url(url),
                'https://www.youtube.com/embed/dQw4w9WgXcQ',
                msg=url,
            )

    def test_rejects_hostile_and_lookalike_urls(self):
        for url in [
            'https://evil.com/?v=dQw4w9WgXcQ',
            'https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ',
            'javascript:alert(1)//?v=dQw4w9WgXcQ',
            'https://www.youtube.com/watch?v=" onload=alert(1) x="',
            'data:text/html,<script>alert(1)</script>',
            '',
            None,
        ]:
            self.assertIsNone(youtube_embed_url(url), msg=url)


class FilterFacetTests(TestCase):
    """Task 1 — facet counts reflect the OTHER filters, not themselves."""

    @classmethod
    def setUpTestData(cls):
        for name, genre, lang in [
            ('A', 'Action', 'Hindi'), ('B', 'Action', 'English'),
            ('C', 'Horror', 'Hindi'), ('D', 'Drama', 'English'),
        ]:
            Movie.objects.create(
                name=name, genre=genre, language=lang,
                rating=7.0, cast='x', image='movies/x.jpg',
            )

    def test_selecting_a_genre_keeps_other_genres_selectable(self):
        response = self.client.get(reverse('movie_list'), {'genre': 'Action'})
        genres = {g['genre'] for g in response.context['genre_counts']}
        self.assertEqual(genres, {'Action', 'Horror', 'Drama'})

    def test_genre_filter_narrows_language_counts(self):
        response = self.client.get(reverse('movie_list'), {'genre': 'Action'})
        counts = {c['language']: c['count'] for c in response.context['language_counts']}
        self.assertEqual(counts, {'Hindi': 1, 'English': 1})

    def test_multi_select_returns_the_union(self):
        response = self.client.get(
            reverse('movie_list'), {'genre': ['Action', 'Horror']}
        )
        names = {m.name for m in response.context['movies']}
        self.assertEqual(names, {'A', 'B', 'C'})


class PaymentFulfilmentTests(TestCase):
    """Tasks 4 and 5 — idempotent fulfilment, no double booking."""

    def setUp(self):
        self.user = User.objects.create_user('amulya', 'a@example.com', 'pw')
        movie = Movie.objects.create(
            name='M', genre='Action', language='Hindi',
            rating=8.0, cast='x', image='movies/x.jpg',
        )
        self.theater = Theater.objects.create(
            name='T1', movie=movie, time=timezone.now() + timedelta(days=1)
        )
        self.seats = [
            Seat.objects.create(theater=self.theater, seat_number=f'A{i}')
            for i in range(1, 3)
        ]
        self.payment = Payment.objects.create(
            user=self.user, theater=self.theater,
            razorpay_order_id='order_test_1', amount=40000, status='pending',
        )
        self.payment.seats.set(self.seats)
        for seat in self.seats:
            SeatReservation.objects.create(
                seat=seat, user=self.user,
                expires_at=timezone.now() + timedelta(minutes=2),
            )

    def test_fulfilment_creates_bookings_and_clears_reservations(self):
        result = fulfil_payment(self.payment.id, 'pay_abc')
        self.assertIsNotNone(result)
        self.assertEqual(Booking.objects.count(), 2)
        self.assertEqual(SeatReservation.objects.count(), 0)
        self.assertTrue(all(s.is_booked for s in Seat.objects.all()))
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'success')

    def test_second_fulfilment_is_a_no_op(self):
        """A duplicate webhook must not create a second set of bookings."""
        self.assertIsNotNone(fulfil_payment(self.payment.id, 'pay_abc'))
        self.assertIsNone(fulfil_payment(self.payment.id, 'pay_abc'))
        self.assertEqual(Booking.objects.count(), 2)


@override_settings(RAZORPAY_WEBHOOK_SECRET='testsecret')
class WebhookSecurityTests(TestCase):
    """Task 4 — signature verification and replay safety."""

    def setUp(self):
        self.user = User.objects.create_user('amulya', 'a@example.com', 'pw')
        movie = Movie.objects.create(
            name='M', genre='Action', language='Hindi',
            rating=8.0, cast='x', image='movies/x.jpg',
        )
        self.theater = Theater.objects.create(
            name='T1', movie=movie, time=timezone.now() + timedelta(days=1)
        )
        self.seat = Seat.objects.create(theater=self.theater, seat_number='A1')
        self.payment = Payment.objects.create(
            user=self.user, theater=self.theater,
            razorpay_order_id='order_test_1', amount=20000, status='pending',
        )
        self.payment.seats.set([self.seat])
        self.url = reverse('razorpay_webhook')

    def _body(self):
        return json.dumps({
            'event': 'payment.captured',
            'payload': {'payment': {'entity': {
                'id': 'pay_abc', 'order_id': 'order_test_1',
            }}},
        })

    def _sign(self, body):
        return hmac.new(b'testsecret', body.encode(), hashlib.sha256).hexdigest()

    def test_valid_signature_fulfils_the_booking(self):
        body = self._body()
        response = self.client.post(
            self.url, body, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=self._sign(body),
        )
        self.assertEqual(response.status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'success')
        self.assertEqual(Booking.objects.count(), 1)

    def test_forged_signature_is_rejected(self):
        response = self.client.post(
            self.url, self._body(), content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE='deadbeef',
        )
        self.assertEqual(response.status_code, 400)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'pending')
        self.assertEqual(Booking.objects.count(), 0)

    def test_replayed_webhook_does_not_double_book(self):
        body = self._body()
        signature = self._sign(body)
        for _ in range(3):
            self.client.post(
                self.url, body, content_type='application/json',
                HTTP_X_RAZORPAY_SIGNATURE=signature,
            )
        self.assertEqual(Booking.objects.count(), 1)

    @override_settings(RAZORPAY_WEBHOOK_SECRET=None)
    def test_unconfigured_secret_fails_closed(self):
        """Without a secret we cannot authenticate, so we must refuse."""
        response = self.client.post(
            self.url, self._body(), content_type='application/json'
        )
        self.assertEqual(response.status_code, 503)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'pending')


class SeatReservationTests(TestCase):
    """Task 5 — a seat already held by someone else cannot be re-reserved."""

    def setUp(self):
        self.alice = User.objects.create_user('alice', 'al@example.com', 'pw')
        self.bob = User.objects.create_user('bob', 'bo@example.com', 'pw')
        movie = Movie.objects.create(
            name='M', genre='Action', language='Hindi',
            rating=8.0, cast='x', image='movies/x.jpg',
        )
        self.theater = Theater.objects.create(
            name='T1', movie=movie, time=timezone.now() + timedelta(days=1)
        )
        self.seat = Seat.objects.create(theater=self.theater, seat_number='A1')

    def test_second_user_cannot_take_a_held_seat(self):
        SeatReservation.objects.create(
            seat=self.seat, user=self.alice,
            expires_at=timezone.now() + timedelta(minutes=2),
        )
        self.client.force_login(self.bob)
        response = self.client.post(
            reverse('book_seats', args=[self.theater.id]),
            {'seats': [self.seat.id]},
        )
        self.assertIn('unavailable', response.context['error'].lower())
        self.assertEqual(SeatReservation.objects.get(seat=self.seat).user, self.alice)

    def test_expired_hold_is_released_and_reusable(self):
        SeatReservation.objects.create(
            seat=self.seat, user=self.alice,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.client.force_login(self.bob)
        self.client.post(
            reverse('book_seats', args=[self.theater.id]),
            {'seats': [self.seat.id]},
        )
        self.assertEqual(SeatReservation.objects.get(seat=self.seat).user, self.bob)
