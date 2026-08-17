from django.shortcuts import render, redirect, get_object_or_404
from .models import Movie, Theater, Seat, Booking, SeatReservation, Payment
from .utils import youtube_embed_url
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.core.cache import cache
from django.db.models import Sum, Count
from django.core.mail import send_mail
from django.db.models.functions import TruncHour
from datetime import timedelta
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse
import razorpay
import hashlib
import hmac
import json
import threading
import time
import logging

logger = logging.getLogger(__name__)

def movie_list(request):
    base = Movie.objects.all()

    search_query = request.GET.get('search')
    if search_query:
        base = base.filter(name__icontains=search_query)

    genres = request.GET.getlist('genre')
    languages = request.GET.getlist('language')

    movies = base
    if genres:
        movies = movies.filter(genre__in=genres)
    if languages:
        movies = movies.filter(language__in=languages)

    sort = request.GET.get('sort', 'name')
    if sort == 'rating':
        movies = movies.order_by('-rating')
    else:
        movies = movies.order_by('name')

    from django.core.paginator import Paginator
    # Facet counts. Each facet is counted against every filter EXCEPT itself,
    # so picking "Action" leaves the other genres visible and clickable —
    # otherwise the multi-select collapses to whatever is already chosen.
    genre_source = base.filter(language__in=languages) if languages else base
    language_source = base.filter(genre__in=genres) if genres else base
    genre_counts = genre_source.order_by().values('genre').annotate(count=Count('id'))
    language_counts = language_source.order_by().values('language').annotate(count=Count('id'))

    #pagination
    paginator = Paginator(movies, 9) #this shows movies per page(9)
    page_number= request.GET.get('page')
    movies = paginator.get_page(page_number)

    return render(request, 'movies/movie_list.html', {
        'movies': movies,
        'genre_counts': genre_counts,
        'language_counts': language_counts,
        'selected_genres': genres,
        'selected_languages': languages,
        'selected_sort': sort,
    })

def movie_detail(request, movie_id):
    movie = get_object_or_404(Movie, id=movie_id)

    # Validated + normalised in movies.utils so the template never receives
    # anything but a youtube.com/embed/ URL we built ourselves.
    embed_url = youtube_embed_url(movie.trailer_url)

    # fetch theaters with select_related so it loads instantly without n+1 queries
    theaters = Theater.objects.filter(movie=movie).select_related('movie')
    return render(request, 'movies/movie_detail.html', {
        'movie': movie,
        'theaters': theaters, 
        'embed_url': embed_url,
    })

def theater_list(request,movie_id):
    movie = get_object_or_404(Movie,id=movie_id)
    # optimizing query for faster rendering
    theater = Theater.objects.filter(movie=movie).select_related('movie')
    return render(request,'movies/theater_list.html',{'movie':movie,'theaters':theater})

@login_required(login_url='/login/')    
def book_seats(request, theater_id):  # Change from booking_Seats to book_seats
    theaters = get_object_or_404(Theater, id=theater_id)
    seats = Seat.objects.filter(theater=theaters)
    
    SeatReservation.objects.filter(expires_at__lt=timezone.now()).delete()

    if request.method == 'POST':
        selected_Seats = request.POST.getlist('seats')
        error_seats = []

        if not selected_Seats:
            reserved_seat_ids = list(
                SeatReservation.objects.filter(
                    seat__theater=theaters,
                    expires_at__gt=timezone.now()
                ).exclude(user=request.user).values_list('seat_id', flat=True)
            )
            booked_seat_ids = set(
                Booking.objects.filter(seat__theater=theaters).values_list('seat_id', flat=True)
            )
            return render(request, 'movies/seat_selection.html', {
                'theaters': theaters,
                'seats': seats,
                'reserved_seat_ids': reserved_seat_ids,
                'booked_seat_ids': booked_seat_ids,
                'error': 'No seats selected'
            })
         
        for seat_id in selected_Seats:
            """seat = get_object_or_404(Seat, id=seat_id, theater=theaters)
            if seat.is_booked:
                error_seats.append(seat.seat_number)
                continue
            try:
                Booking.objects.create(
                    user=request.user,
                    seat = seat,
                    movie = theaters.movie,
                    theater = theaters
                )
                seat.is_booked = True
                seat.save()
            except IntegrityError:
                error_seats.append(seat.seat_number)"""
            try:
                # using atomic transaction so nobody books the same seat at same time
                with transaction.atomic():
                    seat = Seat.objects.select_for_update().get(id=seat_id, theater=theaters)
                    if seat.is_booked:
                        error_seats.append(seat.seat_number)
                        continue

                    # check if seat is reserved by someone else
                    existing = SeatReservation.objects.filter(seat=seat).first()
                    if existing:
                        if not existing.is_expired() and existing.user != request.user:
                            error_seats.append(seat.seat_number)
                            continue
                        existing.delete()

                    #create reservation for 2 min
                    SeatReservation.objects.update_or_create(
                        seat=seat,
                        defaults={
                            'user': request.user,
                            'expires_at': timezone.now() + timedelta(minutes=2)
                        }
                    )
            except Seat.DoesNotExist:
                error_seats.append(seat_id)
            except IntegrityError:
                # Final backstop: SeatReservation.seat / Booking.seat are unique,
                # so a request that loses the race here is rejected by the
                # database rather than silently double-booking.
                error_seats.append(seat_id)
                
        if error_seats:
            reserved_seat_ids = list(
                SeatReservation.objects.filter(
                    seat__theater=theaters,
                    expires_at__gt=timezone.now()
                ).exclude(user=request.user).values_list('seat_id', flat=True)
            )
            booked_seat_ids = set(
                Booking.objects.filter(seat__theater=theaters).values_list('seat_id', flat=True)
            )
            return render(request, 'movies/seat_selection.html', {
                'theaters': theaters,
                'seats': seats,
                'reserved_seat_ids': reserved_seat_ids,
                'booked_seat_ids': booked_seat_ids,
                'error': f"Seats unavailable: {', '.join(str(s) for s in error_seats)}"
            })
        
        return redirect('confirm_booking', theater_id=theater_id)  # Redirect to user profile or booking confirmation page
    reserved_seat_ids = list(
        SeatReservation.objects.filter(
            seat__theater=theaters,
            expires_at__gt=timezone.now()
        ).exclude(user=request.user).values_list('seat_id', flat=True)
    )
    booked_seat_ids = set(
        Booking.objects.filter(seat__theater=theaters).values_list('seat_id', flat=True)
    )
    return render(request, 'movies/seat_selection.html', {
        'theaters': theaters,
        'seats': seats,
        'reserved_seat_ids': reserved_seat_ids,
        'booked_seat_ids': booked_seat_ids
    })

def build_email_context(user, theaters, seat_numbers, payment_id=None, amount=None):
    """Resolve every value the email needs while still on the request thread.

    The sender runs in a background thread, and a thread that touches the ORM
    opens its own database connection that Django never cleans up. Doing the
    lookups here keeps the sender free of database access entirely.
    """
    return {
        'to_email': user.email,
        'username': user.username,
        'movie_name': theaters.movie.name,
        'theater_name': theaters.name,
        'show_time': theaters.time.strftime('%d %b %Y, %I:%M %p'),
        'seat_numbers': ', '.join(seat_numbers),
        'booked_at': timezone.now().strftime('%d %b %Y, %I:%M %p'),
        'payment_id': payment_id,
        'amount': amount,
    }


def send_booking_email(context):
    to_email = context.get('to_email')
    payment_id = context.get('payment_id')
    if not to_email:
        logger.warning("Skipping booking email: user %s has no email address", context.get('username'))
        return

    subject = f"Booking Confirmed - {context['movie_name']}"
    html_message = render_to_string('movies/booking_confirmation_email.html', context)

    # Retry with exponential backoff — a transient SMTP failure usually clears
    # in a few seconds, and hammering the server immediately does not help.
    for attempt in range(3):
        try:
            send_mail(subject, '', None, [to_email], html_message=html_message)
            logger.info("Booking email sent to %s for payment %s", to_email, payment_id)
            return
        except Exception as e:
            logger.warning(
                "Booking email attempt %s/3 failed for %s (payment %s): %s",
                attempt + 1, to_email, payment_id, e,
            )
            if attempt < 2:
                time.sleep(2 ** attempt)

    logger.error(
        "Booking email PERMANENTLY FAILED for %s after 3 attempts (payment %s, seats %s)",
        to_email, payment_id, context.get('seat_numbers'),
    )


def fulfil_payment(payment_id, razorpay_payment_id=None, razorpay_signature=None):
    """Turn a paid order into bookings. Safe to call more than once.

    Both the browser redirect and the Razorpay webhook land here and either
    may arrive first (or twice), so the Payment row is locked and re-checked
    inside the transaction. Returns the Payment on the call that actually
    fulfilled it, or None if another call got there first.
    """
    with transaction.atomic():
        try:
            payment = Payment.objects.select_for_update().get(pk=payment_id)
        except Payment.DoesNotExist:
            return None

        # The idempotency check has to happen under the row lock, otherwise
        # two concurrent callbacks can both read 'pending' and both fulfil.
        if payment.status == 'success':
            return None

        seats = list(Seat.objects.select_for_update().filter(payment=payment))
        for seat in seats:
            if seat.is_booked:
                continue
            Booking.objects.create(
                user=payment.user,
                seat=seat,
                movie=payment.theater.movie,
                theater=payment.theater,
            )
            seat.is_booked = True
            seat.save(update_fields=['is_booked'])

        SeatReservation.objects.filter(seat__in=seats, user=payment.user).delete()

        if razorpay_payment_id:
            payment.razorpay_payment_id = razorpay_payment_id
        if razorpay_signature:
            payment.razorpay_signature = razorpay_signature
        payment.status = 'success'
        payment.save()

    invalidate_dashboard_cache()
    return payment


def invalidate_dashboard_cache():
    cache.delete_many([
        'total_bookings', 'daily_revenue', 'weekly_revenue', 'monthly_revenue',
        'popular_movies', 'busiest_theaters', 'peak_hours', 'cancellation_rate',
    ])


@login_required(login_url='/login/')
def confirm_booking(request, theater_id):
    theaters = get_object_or_404(Theater, id=theater_id)
    
    # Get this user's active reservations for this theater
    reservations = SeatReservation.objects.filter(
        user=request.user,
        seat__theater=theaters,
        expires_at__gt=timezone.now()
    )

    if not reservations.exists():
        return redirect('book_seats', theater_id=theater_id)

    if request.method == 'POST':
        client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        seat_ids = sorted(r.seat_id for r in reservations)
        seat_count = len(seat_ids)
        amount = seat_count * 20000

        # Idempotency: a double-click or refresh must not open a second order
        # for the same seats. Reuse the pending order if one already covers
        # exactly this selection.
        existing = Payment.objects.filter(
            user=request.user, theater=theaters, status='pending'
        ).order_by('-created_at').first()
        if existing and sorted(existing.seats.values_list('id', flat=True)) == seat_ids:
            return render(request, 'movies/payment.html', {
                'theaters': theaters,
                'reservations': reservations,
                'razorpay_key_id': settings.RAZORPAY_KEY_ID,
                'razorpay_order_id': existing.razorpay_order_id,
                'amount': existing.amount,
                'amount_display': existing.amount // 100,
            })

        # receipt doubles as our idempotency key in the Razorpay dashboard,
        # making a duplicate submission easy to spot during reconciliation.
        receipt = f"u{request.user.id}-t{theaters.id}-s{'_'.join(str(s) for s in seat_ids)}"[:40]

        try:
            order = client.order.create({
                'amount': amount,
                'currency': 'INR',
                'payment_capture': 1,
                'receipt': receipt,
            })
        except Exception:
            first_reservation = reservations.first()
            time_remaining = int((first_reservation.expires_at - timezone.now()).total_seconds())
            return render(request, 'movies/confirm_booking.html', {
                'theaters': theaters,
                'reservations': reservations,
                'time_remaining': time_remaining,
                'error': 'Payment service unavailable. Please try again.'
            })

        payment = Payment.objects.create(
            user=request.user,
            theater=theaters,
            razorpay_order_id=order['id'],
            amount=amount,
            status='pending'
        )
        payment.seats.set([r.seat for r in reservations])

        return render(request, 'movies/payment.html', {
            'theaters': theaters,
            'reservations': reservations,
            'razorpay_key_id': settings.RAZORPAY_KEY_ID,
            'razorpay_order_id': order['id'],
            'amount': amount,
            'amount_display': amount // 100,
        })

    # Calculate time remaining
    first_reservation = reservations.first()
    time_remaining = int((first_reservation.expires_at - timezone.now()).total_seconds())
    reserved_seat_ids = list(
        SeatReservation.objects.filter(
            seat__theater=theaters,
            expires_at__gt=timezone.now()
        ).exclude(user=request.user).values_list('seat_id', flat=True)
    )


    return render(request, 'movies/confirm_booking.html', {
        'theaters': theaters,
        'reservations': reservations,
        'time_remaining': time_remaining,
        'reserved_seat_ids': reserved_seat_ids
    })

@login_required(login_url='/login/')
def payment_success(request, theater_id):
    if request.method != 'POST':
        return redirect('movie_list')

    theaters = get_object_or_404(Theater, id=theater_id)
    razorpay_order_id = request.POST.get('razorpay_order_id')
    razorpay_payment_id = request.POST.get('razorpay_payment_id')
    razorpay_signature = request.POST.get('razorpay_signature')

    try:
        payment = Payment.objects.get(razorpay_order_id=razorpay_order_id, user=request.user)
    except Payment.DoesNotExist:
        return render(request, 'movies/payment_failed.html', {'error': 'Invalid payment order.'})

    if payment.status == 'success':
        return redirect('profile')

    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
    try:
        client.utility.verify_payment_signature({
            'razorpay_order_id': razorpay_order_id,
            'razorpay_payment_id': razorpay_payment_id,
            'razorpay_signature': razorpay_signature,
        })
    except razorpay.errors.SignatureVerificationError:
        payment.status = 'failed'
        payment.save()
        return render(request, 'movies/payment_failed.html', {'error': 'Payment verification failed.'})

    seat_numbers = [str(s.seat_number) for s in payment.seats.all()]
    fulfilled = fulfil_payment(payment.id, razorpay_payment_id, razorpay_signature)

    # Only the call that actually fulfilled the order sends mail, so a webhook
    # arriving first does not produce a duplicate confirmation.
    if fulfilled is not None:
        email_context = build_email_context(
            request.user, theaters, seat_numbers,
            razorpay_payment_id, payment.amount // 100,
        )
        thread = threading.Thread(target=send_booking_email, args=(email_context,))
        thread.daemon = True
        thread.start()

    return redirect('profile')


@login_required(login_url='/login/')
def payment_failed(request, theater_id):
    theaters = get_object_or_404(Theater, id=theater_id)
    razorpay_order_id = request.POST.get('razorpay_order_id') or request.GET.get('razorpay_order_id')

    if razorpay_order_id:
        Payment.objects.filter(
            razorpay_order_id=razorpay_order_id,
            user=request.user
        ).update(status='failed')
        cache.delete('cancellation_rate')

    return render(request, 'movies/payment_failed.html', {
        'theaters': theaters,
        'error': 'Payment was not completed. Your seats are still reserved for 2 minutes.'
    })


@csrf_exempt
def razorpay_webhook(request):
    # webhook handles async payments if user closes browser
    if request.method != 'POST':
        return HttpResponse(status=405)

    # Fail CLOSED. If the secret is not configured we cannot authenticate the
    # caller, so we must reject rather than trust an unsigned payload —
    # otherwise anyone who knows this URL could mark orders as paid.
    webhook_secret = getattr(settings, 'RAZORPAY_WEBHOOK_SECRET', None)
    if not webhook_secret:
        logger.error("Razorpay webhook received but RAZORPAY_WEBHOOK_SECRET is not set; rejecting.")
        return HttpResponse(status=503)

    webhook_signature = request.headers.get('X-Razorpay-Signature', '')
    expected = hmac.new(
        webhook_secret.encode(),
        request.body,
        hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, webhook_signature):
        logger.warning("Razorpay webhook signature mismatch; rejecting.")
        return HttpResponse(status=400)

    try:
        payload = json.loads(request.body)
        event = payload.get('event')
        payment_entity = payload.get('payload', {}).get('payment', {}).get('entity', {})
        order_id = payment_entity.get('order_id')
        payment_id = payment_entity.get('id')

        if not order_id:
            return HttpResponse(status=200)

        try:
            payment = Payment.objects.get(razorpay_order_id=order_id)
        except Payment.DoesNotExist:
            return HttpResponse(status=200)

        if event == 'payment.captured':
            # Full fulfilment, not just a status flip — if the user closed the
            # browser before the redirect, this is the only thing that will
            # ever create their Booking rows. fulfil_payment is idempotent, so
            # a duplicate webhook delivery is a no-op.
            seat_numbers = [str(s.seat_number) for s in payment.seats.all()]
            email_context = build_email_context(
                payment.user, payment.theater, seat_numbers,
                payment_id, payment.amount // 100,
            )
            fulfilled = fulfil_payment(payment.id, payment_id)
            if fulfilled is not None:
                logger.info("Payment %s fulfilled via webhook", order_id)
                thread = threading.Thread(target=send_booking_email, args=(email_context,))
                thread.daemon = True
                thread.start()
        elif event == 'payment.failed' and payment.status == 'pending':
            payment.status = 'failed'
            payment.save()
            invalidate_dashboard_cache()

    except (json.JSONDecodeError, KeyError):
        return HttpResponse(status=400)

    return HttpResponse(status=200)


def is_admin(user):
    return user.is_authenticated and user.is_staff

@user_passes_test(is_admin, login_url='/login/')
def admin_dashboard(request):
    today = timezone.now()

    # caching the revenue for 5 mins to prevent admin dashboard from lagging
    daily_revenue = cache.get('daily_revenue')
    if daily_revenue is None:
        daily_revenue = Booking.objects.filter(
            booked_at__gte=today - timedelta(days=1)
        ).aggregate(total=Sum('price'))['total'] or 0
        cache.set('daily_revenue', daily_revenue, 300)

    weekly_revenue = cache.get('weekly_revenue')
    if weekly_revenue is None:
        weekly_revenue = Booking.objects.filter(
            booked_at__gte=today - timedelta(days=7)
        ).aggregate(total=Sum('price'))['total'] or 0
        cache.set('weekly_revenue', weekly_revenue, 300)

    monthly_revenue = cache.get('monthly_revenue')
    if monthly_revenue is None:
        monthly_revenue = Booking.objects.filter(
            booked_at__gte=today - timedelta(days=30)
        ).aggregate(total=Sum('price'))['total'] or 0
        cache.set('monthly_revenue', monthly_revenue, 300)

    popular_movies = cache.get('popular_movies')
    if popular_movies is None:
        popular_movies = list(
            Booking.objects.values('movie__name')
            .annotate(booking_count=Count('id'))
            .order_by('-booking_count')[:5]
        )
        cache.set('popular_movies', popular_movies, 300)

    busiest_theaters = cache.get('busiest_theaters')
    if busiest_theaters is None:
        busiest_theaters = list(
            Booking.objects.values('theater__name', 'theater__id')
            .annotate(booked_seats=Count('id'))
            .order_by('-booked_seats')[:5]
        )
        # One grouped query for the seat totals instead of a COUNT per theater
        # inside the loop (was N+1).
        seat_totals = dict(
            Seat.objects.filter(theater_id__in=[t['theater__id'] for t in busiest_theaters])
            .values_list('theater_id')
            .annotate(total=Count('id'))
        )
        for t in busiest_theaters:
            total_seats = seat_totals.get(t['theater__id'], 0)
            t['occupancy_rate'] = round((t['booked_seats'] / total_seats * 100), 1) if total_seats > 0 else 0
        cache.set('busiest_theaters', busiest_theaters, 300)

    peak_hours = cache.get('peak_hours')
    if peak_hours is None:
        # Bounded to 30 days so this stays an indexed range scan on booked_at
        # rather than grouping the entire bookings table.
        peak_hours = list(
            Booking.objects.filter(booked_at__gte=today - timedelta(days=30))
            .annotate(hour=TruncHour('booked_at'))
            .values('hour')
            .annotate(count=Count('id'))
            .order_by('-count')[:5]
        )
        cache.set('peak_hours', peak_hours, 300)

    total_bookings = cache.get('total_bookings')
    if total_bookings is None:
        total_bookings = Booking.objects.count()
        cache.set('total_bookings', total_bookings, 300)

    cancellation_rate = cache.get('cancellation_rate')
    if cancellation_rate is None:
        total_payments = Payment.objects.count()
        if total_payments > 0:
            failed_cancelled = Payment.objects.filter(status__in=['failed', 'cancelled']).count()
            cancellation_rate = round((failed_cancelled / total_payments) * 100, 2)
        else:
            cancellation_rate = 0.0
        cache.set('cancellation_rate', cancellation_rate, 300)

    return render(request, 'movies/admin_dashboard.html', {
        'daily_revenue': daily_revenue,
        'weekly_revenue': weekly_revenue,
        'monthly_revenue': monthly_revenue,
        'popular_movies': popular_movies,
        'busiest_theaters': busiest_theaters,
        'peak_hours': peak_hours,
        'total_bookings': total_bookings,
        'cancellation_rate': cancellation_rate,
    })
