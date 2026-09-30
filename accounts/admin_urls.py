from django.urls import path

from accounts.admin_views import RoleListView, UserAccessView, UserDetailView, UserListView

urlpatterns = [
    path("roles/", RoleListView.as_view(), name="admin-roles"),
    path("users/", UserListView.as_view(), name="admin-users"),
    path("users/<uuid:uuid>/", UserDetailView.as_view(), name="admin-user-detail"),
    path("users/<uuid:uuid>/access/", UserAccessView.as_view(), name="admin-user-access"),
]
