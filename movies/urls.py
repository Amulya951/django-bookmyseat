from django.urls import path
from . import views

urlpatterns = [
    path('', views.movie_list, name='movie_list'),
    path('<int:movie_id>/', views.movie_detail, name='movie_detail'),
    path('<int:movie_id>/theaters/', views.theater_list, name='theater_list'),
    path('theater/<int:theater_id>/seats/book/', views.book_seats, name='book_seats'),
    path('theater/<int:theater_id>/confirm/', views.confirm_booking, name='confirm_booking'),
    path('admin-dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('theater/<int:theater_id>/payment/success/', views.payment_success, name='payment_success'),
    path('theater/<int:theater_id>/payment/failed/', views.payment_failed, name='payment_failed'),
    path('webhook/razorpay/', views.razorpay_webhook, name='razorpay_webhook'),

]