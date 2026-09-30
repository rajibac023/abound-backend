from uuid import UUID

from django.db.models import QuerySet
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.request import Request
from rest_framework.response import Response

from accounts.admin_views import AdministrativeView
from accounts.models import User
from accounts.policy import Permission
from accounts.serializers import UserSerializer
from customers import services
from customers.selectors import customer_queryset, filter_customers
from customers.serializers import CustomerQuerySerializer


class AdminCustomerListView(AdministrativeView, ListAPIView):
    required_permissions = {"GET": Permission.CUSTOMERS_VIEW, "OPTIONS": Permission.CUSTOMERS_VIEW}
    serializer_class = UserSerializer

    def get_queryset(self) -> QuerySet[User]:
        filters = CustomerQuerySerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        return filter_customers(customer_queryset(), filters.validated_data)


class AdminCustomerDetailView(AdministrativeView, RetrieveAPIView):
    required_permissions = {
        "GET": Permission.CUSTOMERS_VIEW,
        "OPTIONS": Permission.CUSTOMERS_VIEW,
        "PATCH": Permission.CUSTOMERS_UPDATE,
    }
    queryset = customer_queryset()
    serializer_class = UserSerializer
    lookup_field = "uuid"

    def patch(self, request: Request, uuid: UUID) -> Response:
        customer = services.update_customer(
            actor=request.user, customer_uuid=uuid, data=request.data
        )
        return Response(UserSerializer(customer).data)
