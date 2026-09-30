from django.urls import path

from accounts import views

urlpatterns = [
    path("csrf/", views.CSRFView.as_view(), name="auth-csrf"),
    path("register/", views.RegisterView.as_view(), name="auth-register"),
    path("register/code/", views.RegistrationCodeView.as_view(), name="auth-register-code"),
    path("login/", views.LoginView.as_view(), name="auth-login"),
    path("refresh/", views.RefreshView.as_view(), name="auth-refresh"),
    path("logout/", views.LogoutView.as_view(), name="auth-logout"),
    path("me/", views.CurrentUserView.as_view(), name="auth-me"),
    path("permissions/", views.CurrentPermissionsView.as_view(), name="auth-permissions"),
    path("password/change/", views.ChangePasswordView.as_view(), name="auth-password-change"),
    path("password/reset/", views.PasswordResetView.as_view(), name="auth-password-reset"),
    path(
        "password/reset/confirm/",
        views.PasswordResetConfirmView.as_view(),
        name="auth-password-reset-confirm",
    ),
    path(
        "password/reset/open/",
        views.PasswordResetOpenView.as_view(),
        name="auth-password-reset-open",
    ),
]
