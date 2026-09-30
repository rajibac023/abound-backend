from django.db import models


class VehicleStatus(models.TextChoices):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    MAINTENANCE = "MAINTENANCE"
    INACTIVE = "INACTIVE"


class VehicleType(models.TextChoices):
    HATCHBACK = "HATCHBACK"
    SEDAN = "SEDAN"
    SUV = "SUV"
    CROSSOVER = "CROSSOVER"
    MPV = "MPV"
    VAN = "VAN"
    PICKUP = "PICKUP"
    MINIBUS = "MINIBUS"
    BUS = "BUS"
    OTHER = "OTHER"


class FuelType(models.TextChoices):
    PETROL = "PETROL"
    DIESEL = "DIESEL"
    ELECTRIC = "ELECTRIC"
    HYBRID = "HYBRID"
    PLUGIN_HYBRID = "PLUGIN_HYBRID"
    LPG = "LPG"
    CNG = "CNG"
    OTHER = "OTHER"


class Transmission(models.TextChoices):
    MANUAL = "MANUAL"
    AUTOMATIC = "AUTOMATIC"
    CVT = "CVT"
    DCT = "DCT"
    SINGLE_SPEED = "SINGLE_SPEED"
    OTHER = "OTHER"


class DriveType(models.TextChoices):
    FWD = "FWD"
    RWD = "RWD"
    AWD = "AWD"
    FOUR_WD = "4WD"


class MileageUnit(models.TextChoices):
    KM_PER_LITRE = "KM_PER_LITRE"
    KM_PER_KWH = "KM_PER_KWH"


class VehicleDocumentType(models.TextChoices):
    REGISTRATION = "REGISTRATION"
    INSURANCE = "INSURANCE"
    TAX = "TAX"
    FITNESS = "FITNESS"
    EMISSION = "EMISSION"
    OTHER = "OTHER"


class VehicleImageType(models.TextChoices):
    MAIN = "MAIN"
    FRONT = "FRONT"
    REAR = "REAR"
    LEFT_SIDE = "LEFT_SIDE"
    RIGHT_SIDE = "RIGHT_SIDE"
    INTERIOR = "INTERIOR"
    DASHBOARD = "DASHBOARD"
    SEATS = "SEATS"
    BOOT = "BOOT"
    ENGINE = "ENGINE"
    OTHER = "OTHER"
