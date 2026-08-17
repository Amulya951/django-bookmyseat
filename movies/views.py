from django.shortcuts import render, redirect, get_object_or_404
from .models import Movie, Theater, Seat, Booking, SeatReservation, Payment
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
import re
import logging

logger = logging.getLogger(__name__)

def movie_list(request):
    movies = Movie.objects.all()

    search_query = request.GET.get('search')
    if search_query:
        movies = movies.filter(name__icontains=search_query)
    
    genres = request.GET.getlist ('genre')
    if genres:
        movies = movies.filter(genre__in=genres)
    
    languages = request.GET.getlist('language')
    if languages:
        movies = movies.filter(language__in=languages)
        
    sort = request.GET.get('sort', 'name')
    if sort == 'rating':
        movies = movies.order_by('-rating')
    else:
        movies = movies.order_by('name')

    from django.core.paginator import Paginator
    # Get counts for genres and languages without making it slow
    filtered_movies = movies.order_by()  #for grouping the count of genres and languages 
    genre_counts = filtered_movies.values('genre').annotate(count=Count('id'))
    language_counts = filtered_movies.values('language').annotate(count=Count('id'))
    
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

    embed_url = None
    if movie.trailer_url:
        match = re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]+)', movie.trailer_url)
        if match:
            video_id = match.group(1)
            embed_url = f"https://www.youtube.com/embed/{video_id}"
    
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

def send_booking_email(user, theaters, seat_numbers):
    for attempt in range(3):
        try:
            subject = f"Booking Confirmed - {theaters.movie.name}"
            html_message = render_to_string('movies/booking_confirmation_email.html', {
                'username': user.username,
                'movie_name': theaters.movie.name,
                'theater_name': theaters.name,
                'show_time': theaters.time.strftime('%d %b %Y, %I:%M %p'),
                'seat_numbers': ', '.join(seat_numbers),
                'booked_at': timezone.now().strftime('%d %b %Y, %I:%M %p'),
            })
            send_mail(subject, '', None, [user.email], html_message=html_message)
            break
        except Exception as e:
            if attempt == 2:
                logger.error(f"Failed to send booking email to {user.email} after 3 attempts: {e}")


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
        seat_count = reservations.count()
        amount = seat_count * 20000

        try:
            order = client.order.create({
                'amount': amount,
                'currency': 'INR',
                'payment_capture': 1,
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

    # atomic block to make sure payment and booking happen together securely
    with transaction.atomic():
        for seat in payment.seats.all():
            if not seat.is_booked:
                Booking.objects.create(
                    user=request.user,
                    seat=seat,
                    movie=theaters.movie,
                    theater=theaters
                )
                seat.is_booked = True
                seat.save()
        SeatReservation.objects.filter(seat__in=payment.seats.all(), user=request.user).delete()
        payment.razorpay_payment_id = razorpay_payment_id
        payment.razorpay_signature = razorpay_signature
        payment.status = 'success'
        payment.save()

    cache.delete('total_bookings')
    cache.delete('daily_revenue')
    cache.delete('weekly_revenue')
    cache.delete('monthly_revenue')
    cache.delete('popular_movies')
    cache.delete('busiest_theaters')
    cache.delete('peak_hours')
    cache.delete('cancellation_rate')

    seat_numbers = [str(s.seat_number) for s in payment.seats.all()]
    thread = threading.Thread(target=send_booking_email, args=(request.user, theaters, seat_numbers))
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

    webhook_secret = getattr(settings, 'RAZORPAY_WEBHOOK_SECRET', None)
    if webhook_secret:
        webhook_signature = request.headers.get('X-Razorpay-Signature', '')
        expected = hmac.new(
            webhook_secret.encode(),
            request.body,
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, webhook_signature):
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

        if event == 'payment.captured' and payment.status != 'success':
            payment.razorpay_payment_id = payment_id
            payment.status = 'success'
            payment.save()
        elif event == 'payment.failed' and payment.status == 'pending':
            payment.status = 'failed'
            payment.save()

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
        for t in busiest_theaters:
            total_seats = Seat.objects.filter(theater_id=t['theater__id']).count()
            t['occupancy_rate'] = round((t['booked_seats'] / total_seats * 100), 1) if total_seats > 0 else 0
        cache.set('busiest_theaters', busiest_theaters, 300)

    peak_hours = cache.get('peak_hours')
    if peak_hours is None:
        peak_hours = list(
            Booking.objects.annotate(hour=TruncHour('booked_at'))
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
