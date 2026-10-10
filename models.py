import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Boolean, DateTime, Integer, ForeignKey, Enum as SQLEnum, Text, Index
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

def generate_uuid():
    return str(uuid.uuid4())

class Role(str, enum.Enum):
    CLIENT = "CLIENT"
    ADMIN = "ADMIN"
    MAINTENANCE = "MAINTENANCE"

class ClientType(str, enum.Enum):
    CLUB = "CLUB"
    STUDENT = "STUDENT"
    PUBLIC = "PUBLIC"

class EquipmentCondition(str, enum.Enum):
    GOOD = "GOOD"
    DAMAGED = "DAMAGED"
    MISSING = "MISSING"
    PENDING_INSPECTION = "PENDING_INSPECTION"

class BookingStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    AT_RISK = "AT_RISK"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"

class SlotType(str, enum.Enum):
    FIXED = "FIXED"
    CUSTOM = "CUSTOM"

class HandoverType(str, enum.Enum):
    CHECKOUT = "CHECKOUT"
    RETURN = "RETURN"

class KitLocation(str, enum.Enum):
    STORAGE = "STORAGE"
    WITH_CLIENT = "WITH_CLIENT"
    UNDER_REPAIR = "UNDER_REPAIR"

class IncidentStatus(str, enum.Enum):
    OPEN = "OPEN"
    IN_RECOVERY = "IN_RECOVERY"
    RESOLVED = "RESOLVED"

class WaitlistStatus(str, enum.Enum):
    WAITING = "WAITING"
    OFFERED = "OFFERED"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"

class LateReturnCaseStatus(str, enum.Enum):
    AWAITING_CLIENT = "AWAITING_CLIENT"
    CLIENT_ACCEPTED = "CLIENT_ACCEPTED"
    CLIENT_DECLINED = "CLIENT_DECLINED"
    ADMIN_DECIDED = "ADMIN_DECIDED"

class User(Base):
    __tablename__ = "users"

    user_id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)
    role = Column(SQLEnum(Role), nullable=False, default=Role.CLIENT)
    client_type = Column(SQLEnum(ClientType), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    bookings = relationship("Booking", back_populates="client", foreign_keys="Booking.client_id")
    waitlist_entries = relationship("WaitlistEntry", back_populates="client", foreign_keys="WaitlistEntry.client_id")
    notifications = relationship("Notification", back_populates="recipient")


class EventKit(Base):
    __tablename__ = "kits"

    kit_id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, nullable=False, unique=True)
    location = Column(SQLEnum(KitLocation), nullable=False, default=KitLocation.STORAGE)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    equipment = relationship("Equipment", back_populates="kit", cascade="all, delete-orphan")
    bookings = relationship("Booking", back_populates="kit")
    incidents = relationship("Incident", back_populates="kit")

    @property
    def is_ready(self) -> bool:
        """Derived readiness rule (SDD Section 15.1):
        True only if every required equipment item is GOOD and no un-inspected open incident exists.
        """
        for eq in self.equipment:
            if eq.is_required and eq.condition != EquipmentCondition.GOOD:
                return False
        return True


class Equipment(Base):
    __tablename__ = "equipment"

    equipment_id = Column(String, primary_key=True, default=generate_uuid)
    kit_id = Column(String, ForeignKey("kits.kit_id"), nullable=False)
    type = Column(String, nullable=False)  # camera, microphone, tripod, mixer, etc.
    serial_label = Column(String, nullable=True)
    condition = Column(SQLEnum(EquipmentCondition), nullable=False, default=EquipmentCondition.GOOD)
    is_required = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    kit = relationship("EventKit", back_populates="equipment")
    snapshots = relationship("HandoverEquipmentSnapshot", back_populates="equipment")

    @property
    def is_usable(self) -> bool:
        return self.condition == EquipmentCondition.GOOD


class Booking(Base):
    __tablename__ = "bookings"

    booking_id = Column(String, primary_key=True, default=generate_uuid)
    client_id = Column(String, ForeignKey("users.user_id"), nullable=False)
    kit_id = Column(String, ForeignKey("kits.kit_id"), nullable=True)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    held_until = Column(DateTime, nullable=False)  # end_time + 15 mins gap (SDD Section 13.2)
    slot_type = Column(SQLEnum(SlotType), nullable=False, default=SlotType.CUSTOM)
    status = Column(SQLEnum(BookingStatus), nullable=False, default=BookingStatus.PENDING)
    purpose = Column(Text, nullable=True)
    decision_reason = Column(Text, nullable=True)
    revised_handover_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    version = Column(Integer, nullable=False, default=1)

    client = relationship("User", back_populates="bookings", foreign_keys=[client_id])
    kit = relationship("EventKit", back_populates="bookings")
    handover_records = relationship("HandoverRecord", back_populates="booking")
    events = relationship("BookingEvent", back_populates="booking", order_by="BookingEvent.created_at")
    incidents = relationship("Incident", back_populates="booking")

    def cancellation_cutoff(self) -> datetime:
        """Cancellation cutoff is exactly start_time - 2 hours (SDD Section 16.5)"""
        from datetime import timedelta
        return self.start_time - timedelta(hours=2)

    def is_blocking(self) -> bool:
        """Blocking statuses reserve the calendar (SDD Section 14.1)"""
        return self.status in (BookingStatus.CONFIRMED, BookingStatus.AT_RISK)


class HandoverRecord(Base):
    __tablename__ = "handover_records"

    handover_id = Column(String, primary_key=True, default=generate_uuid)
    booking_id = Column(String, ForeignKey("bookings.booking_id"), nullable=False)
    kit_id = Column(String, ForeignKey("kits.kit_id"), nullable=False)
    admin_id = Column(String, ForeignKey("users.user_id"), nullable=False)
    handover_type = Column(SQLEnum(HandoverType), nullable=False)
    recorded_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    notes = Column(Text, nullable=True)

    booking = relationship("Booking", back_populates="handover_records")
    admin = relationship("User")
    snapshots = relationship("HandoverEquipmentSnapshot", back_populates="handover", cascade="all, delete-orphan")


class HandoverEquipmentSnapshot(Base):
    __tablename__ = "handover_equipment_snapshots"

    snapshot_id = Column(String, primary_key=True, default=generate_uuid)
    handover_id = Column(String, ForeignKey("handover_records.handover_id"), nullable=False)
    equipment_id = Column(String, ForeignKey("equipment.equipment_id"), nullable=False)
    condition_at_inspection = Column(SQLEnum(EquipmentCondition), nullable=False)
    notes = Column(Text, nullable=True)

    handover = relationship("HandoverRecord", back_populates="snapshots")
    equipment = relationship("Equipment", back_populates="snapshots")


class Incident(Base):
    __tablename__ = "incidents"

    incident_id = Column(String, primary_key=True, default=generate_uuid)
    kit_id = Column(String, ForeignKey("kits.kit_id"), nullable=False)
    equipment_id = Column(String, ForeignKey("equipment.equipment_id"), nullable=True)
    booking_id = Column(String, ForeignKey("bookings.booking_id"), nullable=True)
    description = Column(Text, nullable=False)
    status = Column(SQLEnum(IncidentStatus), nullable=False, default=IncidentStatus.OPEN)
    resolution = Column(Text, nullable=True)
    reported_by = Column(String, ForeignKey("users.user_id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    resolved_at = Column(DateTime, nullable=True)

    kit = relationship("EventKit", back_populates="incidents")
    equipment = relationship("Equipment")
    booking = relationship("Booking", back_populates="incidents")


class WaitlistEntry(Base):
    __tablename__ = "waitlist_entries"

    entry_id = Column(String, primary_key=True, default=generate_uuid)
    client_id = Column(String, ForeignKey("users.user_id"), nullable=False)
    requested_start = Column(DateTime, nullable=False)
    requested_end = Column(DateTime, nullable=False)
    joined_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    status = Column(SQLEnum(WaitlistStatus), nullable=False, default=WaitlistStatus.WAITING)
    offered_at = Column(DateTime, nullable=True)
    offer_expires_at = Column(DateTime, nullable=True)
    decided_by_admin_id = Column(String, ForeignKey("users.user_id"), nullable=True)
    resulting_booking_id = Column(String, ForeignKey("bookings.booking_id"), nullable=True)

    client = relationship("User", back_populates="waitlist_entries", foreign_keys=[client_id])


class Notification(Base):
    __tablename__ = "notifications"

    notification_id = Column(String, primary_key=True, default=generate_uuid)
    recipient_id = Column(String, ForeignKey("users.user_id"), nullable=False)
    type = Column(String, nullable=False)  # BOOKING_CONFIRMED, LATE_RETURN, etc.
    message = Column(Text, nullable=False)
    booking_id = Column(String, ForeignKey("bookings.booking_id"), nullable=True)
    incident_id = Column(String, ForeignKey("incidents.incident_id"), nullable=True)
    is_read = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    read_at = Column(DateTime, nullable=True)

    recipient = relationship("User", back_populates="notifications")


class BookingEvent(Base):
    __tablename__ = "booking_events"

    event_id = Column(String, primary_key=True, default=generate_uuid)
    booking_id = Column(String, ForeignKey("bookings.booking_id"), nullable=False)
    actor_id = Column(String, ForeignKey("users.user_id"), nullable=True)
    event_type = Column(String, nullable=False)  # CREATED, CONFIRMED, REJECTED, CANCELLED, etc.
    from_status = Column(SQLEnum(BookingStatus), nullable=True)
    to_status = Column(SQLEnum(BookingStatus), nullable=True)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    booking = relationship("Booking", back_populates="events")
    actor = relationship("User")


class LateReturnCase(Base):
    __tablename__ = "late_return_cases"

    case_id = Column(String, primary_key=True, default=generate_uuid)
    kit_id = Column(String, ForeignKey("kits.kit_id"), nullable=False)
    overdue_booking_id = Column(String, ForeignKey("bookings.booking_id"), nullable=False)
    affected_booking_id = Column(String, ForeignKey("bookings.booking_id"), nullable=True)
    status = Column(SQLEnum(LateReturnCaseStatus), nullable=False, default=LateReturnCaseStatus.AWAITING_CLIENT)
    client_response = Column(String, nullable=True)  # ACCEPTED, REJECTED
    estimated_handover_time = Column(DateTime, nullable=True)
    admin_decision = Column(String, nullable=True)
    decision_note = Column(Text, nullable=True)
    decided_by_admin_id = Column(String, ForeignKey("users.user_id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    responded_at = Column(DateTime, nullable=True)
    decided_at = Column(DateTime, nullable=True)
