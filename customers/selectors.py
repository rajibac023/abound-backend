from typing import Any

from django.db.models import Q, QuerySet

from accounts.models import User
from accounts.roles import Role


def customer_queryset() -> QuerySet[User]:
    return User.objects.filter(role=Role.CUSTOMER)


def filter_customers(queryset: QuerySet[User], filters: dict[str, Any]) -> QuerySet[User]:
    if search := filters.get("search"):
        queryset = queryset.filter(
            Q(email__icontains=search)
            | Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(phone__icontains=search)
        )
    if status := filters.get("status"):
        queryset = queryset.filter(is_active=status == "active")
    if ordering := filters.get("ordering"):
        return queryset.order_by(ordering, "id")
    return queryset.order_by("-created_at", "-id")
