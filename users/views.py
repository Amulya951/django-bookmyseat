from django.contrib.auth.forms import AuthenticationForm,PasswordChangeForm
from .forms import UserRegisterForm,UserUpdateForm,ProfileUpdateForm
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from movies.models import Movie, Booking
from movies.utils import youtube_embed_url

def home(request):
    # fetch all movies for homepage
    movies = list(Movie.objects.all())

    # attach the YouTube embed URL to each movie for hover-to-play trailers
    for movie in movies:
        movie.embed_url = youtube_embed_url(movie.trailer_url)

    # top 6 highest-rated movies for the auto-rotating banner
    banner_movies = Movie.objects.order_by('-rating')[:6]

    return render(request, 'home.html', {
        'movies': movies,
        'banner_movies': banner_movies,
    })
def register(request):
    if request.method == 'POST':
        form = UserRegisterForm(request.POST)
        if form.is_valid():
            form.save()
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password1')
            user = authenticate (username=username, password=password)
            login(request, user)
            return redirect('profile')
    else:
        form = UserRegisterForm()
    return render(request, 'users/register.html', {'form': form})

def login_view(request):
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirect('/')
    else:
        form = AuthenticationForm()
    return render(request, 'users/login.html', {'form': form})

@login_required
def profile(request):
    # fetching user bookings with select_related to stop n+1 queries and make it fast
    bookings = Booking.objects.filter(user=request.user).select_related('movie', 'theater', 'seat')
    if request.method == 'POST':
        u_form = UserUpdateForm(request.POST, instance=request.user)
        if u_form.is_valid():
            u_form.save()
            return redirect('Profile')
    else:
        u_form = UserUpdateForm(instance=request.user)
    return render(request, 'users/profile.html', {'u_form': u_form, 'bookings': bookings})

@login_required
def reset_password(request):
    if request.method == 'POST':
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            form.save()
            return redirect('login')
    else:
        form = PasswordChangeForm(request.user, instance=request.user)
    return render(request, 'users/reset_password.html', {'form': form})