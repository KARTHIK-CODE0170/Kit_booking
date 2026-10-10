from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field
from models import Role, ClientType, EquipmentCondition, BookingStatus, SlotType, KitLocation, IncidentStatus, WaitlistStatus, LateReturnCaseStatus

# Auth
class UserRegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    client_type: ClientType = ClientType.STUDENT

class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    user_id: str
    name: str
    email: str
    role: Role
    client_type: Optional[ClientType] = None
    is_active: bool

    class Config:
        from_attributes = True

# Kits & Equipment
class EquipmentResponse(BaseModel):
    equipment_id: str
    kit_id: str
    type: str
    serial_label: Optional[str] = None
    condition: EquipmentCondition
    is_required: bool

    class Config:
        from_attributes = True

class KitResponse(BaseModel):
    kit_id: str
    name: str
    location: KitLocation
    is_active: bool
    is_ready: bool
    equipment: List[EquipmentResponse] = []

    class Config:
        from_attributes = True

class KitCreateRequest(BaseModel):
    name: str
    location: KitLocation = KitLocation.STORAGE

class EquipmentCreateRequest(BaseModel):
    type: str
    serial_label: Optional[str] = None
    condition: EquipmentCondition = EquipmentCondition.GOOD
    is_required: bool = True

class EquipmentUpdateRequest(BaseModel):
    condition: EquipmentCondition
    notes: Optional[str] = None

# Bookings
class BookingCreateRequest(BaseModel):
    start_time: datetime
    end_time: datetime
    slot_type: SlotType = SlotType.CUSTOM
    purpose: Optional[str] = None

class BookingAssignKitRequest(BaseModel):
    kit_id: str

class BookingConfirmRequest(BaseModel):
    kit_id: str
    expected_version: Optional[int] = None

class BookingRejectRequest(BaseModel):
    reason: str

class BookingCancelRequest(BaseModel):
    reason: Optional[str] = None

class BookingEventResponse(BaseModel):
    event_id: str
    booking_id: str
    actor_id: Optional[str]
    event_type: str
    from_status: Optional[BookingStatus]
    to_status: Optional[BookingStatus]
    reason: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True

class BookingResponse(BaseModel):
    booking_id: str
    client_id: str
    client_name: Optional[str] = None
    kit_id: Optional[str] = None
    kit_name: Optional[str] = None
    start_time: datetime
    end_time: datetime
    held_until: datetime
    slot_type: SlotType
    status: BookingStatus
    purpose: Optional[str] = None
    decision_reason: Optional[str] = None
    revised_handover_time: Optional[datetime] = None
    version: int
    created_at: datetime

    class Config:
        from_attributes = True

# Inspections / Handovers
class InspectedItem(BaseModel):
    equipment_id: str
    condition: EquipmentCondition
    notes: Optional[str] = None

class CheckoutRequest(BaseModel):
    items: List[InspectedItem]

class ReturnRequest(BaseModel):
    items: List[InspectedItem]

class HandoverEquipmentSnapshotResponse(BaseModel):
    snapshot_id: str
    equipment_id: str
    condition_at_inspection: EquipmentCondition
    notes: Optional[str] = None

    class Config:
        from_attributes = True

class HandoverRecordResponse(BaseModel):
    handover_id: str
    booking_id: str
    kit_id: str
    admin_id: str
    handover_type: str
    recorded_at: datetime
    snapshots: List[HandoverEquipmentSnapshotResponse] = []

    class Config:
        from_attributes = True

# Incidents
class IncidentCreateRequest(BaseModel):
    kit_id: str
    equipment_id: Optional[str] = None
    booking_id: Optional[str] = None
    description: str

class IncidentUpdateRequest(BaseModel):
    status: IncidentStatus
    resolution: Optional[str] = None

class IncidentResponse(BaseModel):
    incident_id: str
    kit_id: str
    equipment_id: Optional[str] = None
    booking_id: Optional[str] = None
    description: str
    status: IncidentStatus
    resolution: Optional[str] = None
    reported_by: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None

    class Config:
        from_attributes = True

# Waitlist
class WaitlistJoinRequest(BaseModel):
    requested_start: datetime
    requested_end: datetime

class WaitlistRespondRequest(BaseModel):
    accept: bool

class WaitlistResponse(BaseModel):
    entry_id: str
    client_id: str
    client_name: Optional[str] = None
    requested_start: datetime
    requested_end: datetime
    joined_at: datetime
    status: WaitlistStatus
    offered_at: Optional[datetime] = None
    offer_expires_at: Optional[datetime] = None
    resulting_booking_id: Optional[str] = None

    class Config:
        from_attributes = True

# Late Handover
class LateHandoverRespondRequest(BaseModel):
    accept: bool
    estimated_handover_time: Optional[datetime] = None

class LateReturnDecisionRequest(BaseModel):
    admin_decision: str  # PROCEED_WITH_DELAY, REASSIGN_KIT, OFFER_OTHER_CLIENT, CANCEL_BY_ADMIN
    decision_note: Optional[str] = None
    alternative_kit_id: Optional[str] = None

# Notifications
class NotificationResponse(BaseModel):
    notification_id: str
    recipient_id: str
    type: str
    message: str
    booking_id: Optional[str] = None
    incident_id: Optional[str] = None
    is_read: bool
    created_at: datetime

    class Config:
        from_attributes = True
