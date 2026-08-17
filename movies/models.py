from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta


class Movie(models.Model):
    name = models.CharField(max_length=255)
    image = models.ImageField(upload_to='movies/')
    rating = models.DecimalField(max_digits=3, decimal_places=1)
    cast = models.TextField()
    description = models.TextField(blank=True, null=True) #optional field
    GENRE_CHOICES = [
        ('Action', 'Action'),
        ('Comedy', 'Comedy'),
        ('Drama', 'Drama'),
        ('Horror', 'Horror'),
        ('Sci-Fi', 'Sci-Fi'),
        ('Romance', 'Romance'),
        ('Thriller', 'Thriller'),
        ('Animation', 'Animation'),
        ('Documentary', 'Documentary'),
        ('Sports', 'Sports'),
        ('Family', 'Family'),
        ('Fantasy', 'Fantasy'),
        # Add more genres as needed
    ]
    genre = models.CharField(max_length=100, choices=GENRE_CHOICES, default='null', db_index=True)
    LANGUAGE_CHOICES = [
        ('English', 'English'),
        ('Hindi', 'Hindi'),
        # Add more languages as needed
    ]

    trailer_url = models.URLField(blank=True, null=True) #trailer emmbedding 
    language = models.CharField(max_length=100, choices=LANGUAGE_CHOICES, default='Hindi', db_index=True)   
    def __str__(self):
        return self.name
    
class Theater(models.Model):
    name = models.CharField(max_length=255)
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name='theaters')
    time = models.DateTimeField()  # ← DATE + TIME combined

    def __str__(self):
        return f"{self.name} - {self.movie.name} at {self.time}"

class Seat(models.Model):
    theater = models.ForeignKey(Theater, on_delete=models.CASCADE, related_name='seats')
    seat_number = models.CharField(max_length=10)
    is_booked = models.BooleanField(default=False)
    
    def __str__(self):
        return f"{self.seat_number} - {self.theater.name}"

class SeatReservation(models.Model):
    seat = models.OneToOneField(Seat, on_delete=models.CASCADE, related_name='reservation')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    reserved_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    def is_expired(self):
        return timezone.now() > self.expires_at
    
    def __str__(self):
        return f"{self.seat} reserved by {self.user.username}"
class Booking(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    seat = models.OneToOneField(Seat, on_delete=models.CASCADE)
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE)
    theater = models.ForeignKey(Theater, on_delete=models.CASCADE)
    booked_at = models.DateTimeField(auto_now_add=True, db_index=True)
    price = models.DecimalField(max_digits=8, decimal_places=2, default=200.00)

    def __str__(self):
        return f"{self.user.username} for {self.seat.seat_number} at {self.theater.name}"
    
class Payment(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('success', 'Success'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    theater = models.ForeignKey(Theater, on_delete=models.CASCADE)
    seats = models.ManyToManyField(Seat, blank=True)
    razorpay_order_id = models.CharField(max_length=100, unique=True)
    razorpay_payment_id = models.CharField(max_length=100, blank=True, null=True)
    razorpay_signature = models.CharField(max_length=255, blank=True, null=True)
    amount = models.IntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.razorpay_order_id} - {self.status}"