import os
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import (
    Base, User, Role, ClientType, EventKit, Equipment, EquipmentCondition,
    KitLocation, Booking, BookingStatus, SlotType, Notification
)

DB_PATH = os.path.join(os.path.dirname(__file__), "event_kit.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    Base.metadata.create_all(bind=engine)
    seed_db()

def seed_db():
    db = SessionLocal()
    try:
        # Check if already seeded
        if db.query(User).first():
            return

        # Users
        admin = User(
            user_id="u_admin",
            name="Surya Admin",
            email="admin@eventkit.edu",
            password_hash="admin123",  # Demo simplified hash
            role=Role.ADMIN,
            is_active=True
        )
        client1 = User(
            user_id="u_client1",
            name="Asha R (Media Club)",
            email="asha@eventkit.edu",
            password_hash="client123",
            role=Role.CLIENT,
            client_type=ClientType.CLUB,
            is_active=True
        )
        client2 = User(
            user_id="u_client2",
            name="Rahul M (Student)",
            email="rahul@eventkit.edu",
            password_hash="client123",
            role=Role.CLIENT,
            client_type=ClientType.STUDENT,
            is_active=True
        )

        db.add_all([admin, client1, client2])
        db.commit()

        # Kit A: Premium Media Pack
        kit_a = EventKit(
            kit_id="k_01",
            name="Kit A - 4K Cinema & Audio Pack",
            location=KitLocation.STORAGE,
            is_active=True
        )
        eq_a1 = Equipment(
            equipment_id="eq_a1", kit_id="k_01", type="4K Video Camera", serial_label="CAM-4K-01", condition=EquipmentCondition.GOOD, is_required=True
        )
        eq_a2 = Equipment(
            equipment_id="eq_a2", kit_id="k_01", type="Wireless Lavalier Mic", serial_label="MIC-WL-01", condition=EquipmentCondition.GOOD, is_required=True
        )
        eq_a3 = Equipment(
            equipment_id="eq_a3", kit_id="k_01", type="Carbon Fiber Tripod", serial_label="TRI-CF-01", condition=EquipmentCondition.GOOD, is_required=True
        )

        # Kit B: Stage Audio Kit
        kit_b = EventKit(
            kit_id="k_02",
            name="Kit B - Live Stage Audio Bundle",
            location=KitLocation.STORAGE,
            is_active=True
        )
        eq_b1 = Equipment(
            equipment_id="eq_b1", kit_id="k_02", type="8-Channel Audio Mixer", serial_label="MIX-08-01", condition=EquipmentCondition.GOOD, is_required=True
        )
        eq_b2 = Equipment(
            equipment_id="eq_b2", kit_id="k_02", type="Dynamic Vocal Mic", serial_label="MIC-DV-01", condition=EquipmentCondition.GOOD, is_required=True
        )
        eq_b3 = Equipment(
            equipment_id="eq_b3", kit_id="k_02", type="Active Speaker Pair", serial_label="SPK-AC-01", condition=EquipmentCondition.GOOD, is_required=True
        )

        # Kit C: Podcast & Portable Bundle
        kit_c = EventKit(
            kit_id="k_03",
            name="Kit C - Portable Studio Kit",
            location=KitLocation.STORAGE,
            is_active=True
        )
        eq_c1 = Equipment(
            equipment_id="eq_c1", kit_id="k_03", type="USB Studio Mic", serial_label="MIC-USB-01", condition=EquipmentCondition.GOOD, is_required=True
        )
        eq_c2 = Equipment(
            equipment_id="eq_c2", kit_id="k_03", type="LED Panel Lighting", serial_label="LED-PN-01", condition=EquipmentCondition.GOOD, is_required=True
        )

        db.add_all([kit_a, eq_a1, eq_a2, eq_a3, kit_b, eq_b1, eq_b2, eq_b3, kit_c, eq_c1, eq_c2])
        db.commit()

        # Seed initial sample booking
        now = datetime.now(timezone.utc)
        start1 = now + timedelta(hours=3)
        end1 = start1 + timedelta(hours=2)
        held1 = end1 + timedelta(minutes=15)

        b1 = Booking(
            booking_id="b_seed1",
            client_id="u_client1",
            kit_id="k_01",
            start_time=start1,
            end_time=end1,
            held_until=held1,
            slot_type=SlotType.CUSTOM,
            status=BookingStatus.CONFIRMED,
            purpose="Annual Club Fest Coverage",
            version=1
        )

        db.add(b1)
        db.commit()

        # Welcome notification
        n1 = Notification(
            notification_id="n_welcome",
            recipient_id="u_client1",
            type="SYSTEM_WELCOME",
            message="Welcome to the Event Kit Rental System! Your booking for 'Kit A' is CONFIRMED.",
            booking_id="b_seed1",
            is_read=False
        )
        db.add(n1)
        db.commit()

    finally:
        db.close()
