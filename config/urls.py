from django.urls import include, path

urlpatterns = [path("api/v1/", include("config.api_urls", namespace="v1"))]

handler400 = "common.errors.bad_request"
handler403 = "common.errors.permission_denied"
handler404 = "common.errors.not_found"
handler500 = "common.errors.server_error"
