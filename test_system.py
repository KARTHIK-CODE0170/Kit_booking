import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import (
    Base, User, Role, EventKit, Equipment, EquipmentCondition, Booking,
    BookingStatus, HandoverType, KitLocation, IncidentStatus, WaitlistStatus
)
from services import (
    AvailabilityScheduler, BookingManager, HandoverManager,
    IncidentManager, WaitlistManager
)

@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Seed test users
    admin = User(user_id="u_admin", name="Admin", email="admin@test.com", password_hash="pass", role=Role.ADMIN)
    client1 = User(user_id="u_client1", name="Client 1", email="c1@test.com", password_hash="pass", role=Role.CLIENT)
    client2 = User(user_id="u_client2", name="Client 2", email="c2@test.com", password_hash="pass", role=Role.CLIENT)
    session.add_all([admin, client1, client2])
    session.commit()

    # Seed Kit A with 3 required equipment items
    kit_a = EventKit(kit_id="k_01", name="Kit A", location=KitLocation.STORAGE, is_active=True)
    eq1 = Equipment(equipment_id="eq_1", kit_id="k_01", type="Camera", condition=EquipmentCondition.GOOD, is_required=True)
    eq2 = Equipment(equipment_id="eq_2", kit_id="k_01", type="Mic", condition=EquipmentCondition.GOOD, is_required=True)
    eq3 = Equipment(equipment_id="eq_3", kit_id="k_01", type="Tripod", condition=EquipmentCondition.GOOD, is_required=True)
    session.add_all([kit_a, eq1, eq2, eq3])
    session.commit()

    yield session
    session.close()


def test_15_minute_gap_rule(db_session):
    """SDD T-04, T-05: Test 15-minute scheduling gap per kit"""
    kit = db_session.query(EventKit).filter(EventKit.kit_id == "k_01").first()
    now = datetime.now(timezone.utc) + timedelta(days=1)
    
    start1 = now
    end1 = start1 + timedelta(hours=2) # e.g. 10:00 to 12:00
    held1 = end1 + timedelta(minutes=15)

    # Booking 1: 10:00 - 12:00 (Protected range 10:00 - 12:15)
    b1 = Booking(
        booking_id="b1", client_id="u_client1", kit_id="k_01",
        start_time=start1, end_time=end1, held_until=held1,
        status=BookingStatus.CONFIRMED, version=1
    )
    db_session.add(b1)
    db_session.commit()

    # Request 2: Starts 12:10 (Only 10 mins gap) -> MUST BE REFUSED
    req2_start = end1 + timedelta(minutes=10)
    req2_end = req2_start + timedelta(hours=2)
    eligible, reason = AvailabilityScheduler.is_kit_eligible(db_session, kit, req2_start, req2_end)
    assert not eligible
    assert "15-min inspection gap violated" in reason

    # Request 3: Starts 12:15 (Exactly 15 mins gap) -> MUST BE ELIGIBLE
    req3_start = end1 + timedelta(minutes=15)
    req3_end = req3_start + timedelta(hours=2)
    eligible3, reason3 = AvailabilityScheduler.is_kit_eligible(db_session, kit, req3_start, req3_end)
    assert eligible3


def test_two_hour_cancellation_cutoff(db_session):
    """SDD T-14, T-15: Test 2-hour cancellation cutoff rule"""
    now = datetime.now(timezone.utc)
    
    # Booking starting in 1 hour (past 2h cutoff)
    start_soon = now + timedelta(hours=1)
    end_soon = start_soon + timedelta(hours=2)
    b_soon = Booking(
        booking_id="b_soon", client_id="u_client1", kit_id="k_01",
        start_time=start_soon, end_time=end_soon, held_until=end_soon + timedelta(minutes=15),
        status=BookingStatus.CONFIRMED, version=1
    )
    db_session.add(b_soon)
    db_session.commit()

    # Client cancellation MUST fail past cutoff
    with pytest.raises(ValueError) as exc:
        BookingManager.cancel_booking(db_session, "b_soon", actor_id="u_client1", is_admin=False)
    assert "cutoff passed" in str(exc.value).lower()

    # Admin override MUST succeed
    b_cancelled = BookingManager.cancel_booking(db_session, "b_soon", actor_id="u_admin", is_admin=True, reason="Emergency override")
    assert b_cancelled.status == BookingStatus.CANCELLED


def test_derived_kit_readiness(db_session):
    """SDD T-19: Test derived readiness when component is damaged"""
    kit = db_session.query(EventKit).filter(EventKit.kit_id == "k_01").first()
    assert kit.is_ready

    # Damage 1 component
    eq2 = db_session.query(Equipment).filter(Equipment.equipment_id == "eq_2").first()
    eq2.condition = EquipmentCondition.DAMAGED
    db_session.commit()

    # Kit readiness should now be False automatically!
    assert not kit.is_ready


def test_handover_blocking_on_damaged_component(db_session):
    """SDD T-08, T-19: Checkout blocked if required component not GOOD"""
    now = datetime.now(timezone.utc) + timedelta(hours=3)
    b = Booking(
        booking_id="b_ho", client_id="u_client1", kit_id="k_01",
        start_time=now, end_time=now + timedelta(hours=2), held_until=now + timedelta(hours=2, minutes=15),
        status=BookingStatus.CONFIRMED, version=1
    )
    db_session.add(b)

    # Component 2 damaged
    eq2 = db_session.query(Equipment).filter(Equipment.equipment_id == "eq_2").first()
    eq2.condition = EquipmentCondition.DAMAGED
    db_session.commit()

    # Attempt checkout -> MUST FAIL
    inspected = [
        {"equipment_id": "eq_1", "condition": EquipmentCondition.GOOD},
        {"equipment_id": "eq_2", "condition": EquipmentCondition.DAMAGED},
        {"equipment_id": "eq_3", "condition": EquipmentCondition.GOOD},
    ]
    with pytest.raises(ValueError) as exc:
        HandoverManager.checkout(db_session, "b_ho", admin_id="u_admin", inspected_items=inspected)
    assert "BLOCKED" in str(exc.value)


def test_waitlist_fifo_queue(db_session):
    """SDD T-29: Test FIFO waitlist ordering"""
    now = datetime.now(timezone.utc)
    st = now + timedelta(days=2)
    et = st + timedelta(hours=3)

    e1 = WaitlistManager.join_waitlist(db_session, "u_client1", st, et)
    e2 = WaitlistManager.join_waitlist(db_session, "u_client2", st, et)

    assert e1.joined_at <= e2.joined_at
    assert e1.status == WaitlistStatus.WAITING

    # Offer to earliest entry
    offered = WaitlistManager.offer_slot(db_session, e1.entry_id, "u_admin")
    assert offered.status == WaitlistStatus.OFFERED
