from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple, Dict
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from models import (
    Booking, EventKit, Equipment, EquipmentCondition, BookingStatus,
    HandoverRecord, HandoverEquipmentSnapshot, HandoverType, KitLocation,
    Incident, IncidentStatus, WaitlistEntry, WaitlistStatus, Notification,
    BookingEvent, LateReturnCase, LateReturnCaseStatus
)

GAP_MINUTES = 15

class AvailabilityScheduler:
    @staticmethod
    def is_kit_eligible(
        db: Session,
        kit: EventKit,
        start_time: datetime,
        end_time: datetime,
        exclude_booking_id: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        Enforces Availability & Conflict Prevention Rules (SDD Section 16.4, 17.2, 17.3):
        1. Interval rules: end > start, start not in past, min/max limits
        2. Kit state: active, is_ready (all required components GOOD)
        3. Physical state: not currently checked out for overlapping range
        4. Scheduling conflicts on the SAME kit: protected range [start, end + 15m)
        """

        # 1. Interval checks
        if end_time <= start_time:
            return False, "Invalid interval: end time must be strictly after start time"

        now = datetime.now(timezone.utc)
        # Ensure start_time comparison works with naive/tz-aware
        st_cmp = start_time.replace(tzinfo=timezone.utc) if start_time.tzinfo is None else start_time
        if st_cmp < now - timedelta(minutes=5):  # small grace
            return False, "Interval cannot be in the past"

        # 2. Kit state
        if not kit.is_active:
            return False, "Kit is retired or inactive"

        if not kit.is_ready:
            return False, "Kit is not ready (one or more required components damaged/missing/uninspected)"

        # 3. Scheduling gap check on the SAME kit (Protected range: start to end + 15 min)
        # Two bookings on same kit conflict if: new.start < other.end + 15m AND other.start < new.end + 15m
        new_held_until = end_time + timedelta(minutes=GAP_MINUTES)

        query = db.query(Booking).filter(
            Booking.kit_id == kit.kit_id,
            Booking.status.in_([BookingStatus.CONFIRMED, BookingStatus.AT_RISK])
        )
        if exclude_booking_id:
            query = query.filter(Booking.booking_id != exclude_booking_id)

        blocking_bookings = query.all()

        for b in blocking_bookings:
            # Check overlap between [start_time, new_held_until) and [b.start_time, b.held_until)
            b_start = b.start_time.replace(tzinfo=timezone.utc) if b.start_time.tzinfo is None else b.start_time
            b_held = b.held_until.replace(tzinfo=timezone.utc) if b.held_until.tzinfo is None else b.held_until

            if st_cmp < b_held and b_start < new_held_until:
                return False, f"Conflict on {kit.name} with booking {b.booking_id[:8]} (15-min inspection gap violated)"

        return True, "Eligible"

    @staticmethod
    def find_eligible_kits(
        db: Session,
        start_time: datetime,
        end_time: datetime
    ) -> List[Dict]:
        """Finds all active kits and their eligibility status (SDD Section 17.3)"""
        kits = db.query(EventKit).filter(EventKit.is_active == True).all()
        results = []
        for k in kits:
            eligible, reason = AvailabilityScheduler.is_kit_eligible(db, k, start_time, end_time)
            results.append({
                "kit_id": k.kit_id,
                "name": k.name,
                "is_eligible": eligible,
                "reason": reason,
                "is_ready": k.is_ready,
                "location": k.location
            })
        return results


class NotificationService:
    @staticmethod
    def notify(
        db: Session,
        recipient_id: str,
        type_: str,
        message: str,
        booking_id: Optional[str] = None,
        incident_id: Optional[str] = None
    ) -> Notification:
        """Persisted In-App Notification creation (SDD Section 20.2)"""
        n = Notification(
            recipient_id=recipient_id,
            type=type_,
            message=message,
            booking_id=booking_id,
            incident_id=incident_id,
            is_read=False
        )
        db.add(n)
        db.commit()
        db.refresh(n)
        return n


class BookingManager:
    @staticmethod
    def create_request(
        db: Session,
        client_id: str,
        start_time: datetime,
        end_time: datetime,
        purpose: Optional[str] = None
    ) -> Booking:
        """Creates a PENDING booking request (SDD Section 16.2)"""
        if end_time <= start_time:
            raise ValueError("End time must be after start time")

        held_until = end_time + timedelta(minutes=GAP_MINUTES)
        b = Booking(
            client_id=client_id,
            start_time=start_time,
            end_time=end_time,
            held_until=held_until,
            status=BookingStatus.PENDING,
            purpose=purpose,
            version=1
        )
        db.add(b)
        db.commit()
        db.refresh(b)

        # Audit Event
        evt = BookingEvent(
            booking_id=b.booking_id,
            actor_id=client_id,
            event_type="CREATED",
            to_status=BookingStatus.PENDING,
            reason="Client submitted booking request"
        )
        db.add(evt)
        db.commit()

        # Notify Admins
        admins = db.query(Booking).filter(Booking.client_id == "u_admin").all()
        NotificationService.notify(
            db, "u_admin", "BOOKING_REQUESTED",
            f"New booking request {b.booking_id[:8]} received from client.",
            booking_id=b.booking_id
        )
        return b

    @staticmethod
    def assign_kit(db: Session, booking_id: str, kit_id: str, admin_id: str) -> Booking:
        b = db.query(Booking).filter(Booking.booking_id == booking_id).first()
        if not b or b.status != BookingStatus.PENDING:
            raise ValueError("Booking must be in PENDING status to assign kit")

        kit = db.query(EventKit).filter(EventKit.kit_id == kit_id).first()
        if not kit:
            raise ValueError("Kit not found")

        b.kit_id = kit_id
        b.version += 1
        db.commit()

        evt = BookingEvent(
            booking_id=b.booking_id,
            actor_id=admin_id,
            event_type="KIT_ASSIGNED",
            reason=f"Assigned to {kit.name}"
        )
        db.add(evt)
        db.commit()
        return b

    @staticmethod
    def confirm_booking(
        db: Session,
        booking_id: str,
        kit_id: str,
        admin_id: str,
        expected_version: Optional[int] = None
    ) -> Booking:
        """
        Atomic Confirmation with Recheck & Kit Lock (SDD Section 13.4, 16.3, 17.3):
        Rechecks kit readiness, overlap, and 15-minute gap under transaction before confirming.
        """
        b = db.query(Booking).filter(Booking.booking_id == booking_id).first()
        if not b or b.status != BookingStatus.PENDING:
            raise ValueError("Booking is not PENDING")

        if expected_version is not None and b.version != expected_version:
            raise ValueError("Version conflict: booking was modified by another administrator")

        kit = db.query(EventKit).filter(EventKit.kit_id == kit_id).first()
        if not kit:
            raise ValueError("Kit not found")

        # Recheck eligibility
        eligible, reason = AvailabilityScheduler.is_kit_eligible(db, kit, b.start_time, b.end_time, exclude_booking_id=b.booking_id)
        if not eligible:
            raise ValueError(f"Confirmation refused: {reason}")

        b.kit_id = kit_id
        b.status = BookingStatus.CONFIRMED
        b.version += 1
        db.commit()

        evt = BookingEvent(
            booking_id=b.booking_id,
            actor_id=admin_id,
            event_type="CONFIRMED",
            from_status=BookingStatus.PENDING,
            to_status=BookingStatus.CONFIRMED,
            reason=f"Confirmed and assigned to {kit.name}"
        )
        db.add(evt)
        db.commit()

        # Notify Client after commit
        NotificationService.notify(
            db, b.client_id, "BOOKING_CONFIRMED",
            f"Your booking {b.booking_id[:8]} has been CONFIRMED for kit '{kit.name}'.",
            booking_id=b.booking_id
        )
        return b

    @staticmethod
    def reject_booking(db: Session, booking_id: str, admin_id: str, reason: str) -> Booking:
        b = db.query(Booking).filter(Booking.booking_id == booking_id).first()
        if not b or b.status != BookingStatus.PENDING:
            raise ValueError("Booking is not PENDING")

        b.status = BookingStatus.REJECTED
        b.decision_reason = reason
        b.version += 1
        db.commit()

        evt = BookingEvent(
            booking_id=b.booking_id,
            actor_id=admin_id,
            event_type="REJECTED",
            from_status=BookingStatus.PENDING,
            to_status=BookingStatus.REJECTED,
            reason=reason
        )
        db.add(evt)
        db.commit()

        NotificationService.notify(
            db, b.client_id, "BOOKING_REJECTED",
            f"Your booking request {b.booking_id[:8]} was rejected. Reason: {reason}",
            booking_id=b.booking_id
        )
        return b

    @staticmethod
    def cancel_booking(
        db: Session,
        booking_id: str,
        actor_id: str,
        is_admin: bool = False,
        reason: Optional[str] = None
    ) -> Booking:
        """
        Enforces Two-Hour Cutoff Rule (SDD Section 8.3, 16.5):
        Client can cancel up to and including start_time - 2 hours.
        After cutoff, client is rejected; admin override requires a logged reason.
        """
        b = db.query(Booking).filter(Booking.booking_id == booking_id).first()
        if not b or b.status not in (BookingStatus.CONFIRMED, BookingStatus.PENDING, BookingStatus.AT_RISK):
            raise ValueError("Booking cannot be cancelled in its current state")

        now = datetime.now(timezone.utc)
        cutoff = b.cancellation_cutoff()
        cutoff_cmp = cutoff.replace(tzinfo=timezone.utc) if cutoff.tzinfo is None else cutoff

        if not is_admin:
            if now > cutoff_cmp:
                raise ValueError(f"Cancellation cutoff passed. Cancellations are allowed up to 2 hours before start time ({cutoff_cmp.strftime('%Y-%m-%d %H:%M UTC')}).")

        is_override = (now > cutoff_cmp) and is_admin
        b.status = BookingStatus.CANCELLED
        if reason:
            b.decision_reason = reason
        b.version += 1
        db.commit()

        evt_type = "CANCEL_OVERRIDE" if is_override else "CANCELLED"
        evt = BookingEvent(
            booking_id=b.booking_id,
            actor_id=actor_id,
            event_type=evt_type,
            to_status=BookingStatus.CANCELLED,
            reason=reason or ("Admin override after cutoff" if is_override else "Client cancelled before cutoff")
        )
        db.add(evt)
        db.commit()

        # If kit is currently physically checked out, kit remains unavailable (SDD 16.5 step 6)
        NotificationService.notify(
            db, b.client_id if is_admin else "u_admin", "BOOKING_CANCELLED",
            f"Booking {b.booking_id[:8]} has been CANCELLED.",
            booking_id=b.booking_id
        )
        return b


class HandoverManager:
    @staticmethod
    def checkout(
        db: Session,
        booking_id: str,
        admin_id: str,
        inspected_items: List[Dict]
    ) -> HandoverRecord:
        """
        Checkout Inspection (SDD Section 8.4, 16.6):
        - Every required component MUST pass as GOOD.
        - If any required component is DAMAGED or MISSING or uninspected, checkout is BLOCKED.
        - Creates snapshots and sets kit location to WITH_CLIENT.
        """
        b = db.query(Booking).filter(Booking.booking_id == booking_id).first()
        if not b or b.status not in (BookingStatus.CONFIRMED, BookingStatus.AT_RISK):
            raise ValueError("Booking is not eligible for checkout")

        if not b.kit_id:
            raise ValueError("No kit assigned to booking")

        kit = db.query(EventKit).filter(EventKit.kit_id == b.kit_id).first()
        if not kit:
            raise ValueError("Kit not found")

        # Map inspection items by equipment_id
        inspected_map = {item["equipment_id"]: item for item in inspected_items}

        # Validate components
        blocking_items = []
        for eq in kit.equipment:
            insp = inspected_map.get(eq.equipment_id)
            cond = insp["condition"] if insp else eq.condition
            if eq.is_required and cond != EquipmentCondition.GOOD:
                blocking_items.append({"equipment_id": eq.equipment_id, "type": eq.type, "condition": cond})

        if blocking_items:
            # Block checkout!
            raise ValueError(f"Checkout BLOCKED! Required component(s) unusable: {blocking_items}")

        # Record Checkout
        record = HandoverRecord(
            booking_id=booking_id,
            kit_id=kit.kit_id,
            admin_id=admin_id,
            handover_type=HandoverType.CHECKOUT,
            recorded_at=datetime.now(timezone.utc)
        )
        db.add(record)
        db.commit()

        # Save snapshots & update condition
        for eq in kit.equipment:
            insp = inspected_map.get(eq.equipment_id)
            cond = insp["condition"] if insp else eq.condition
            notes = insp.get("notes") if insp else None

            snap = HandoverEquipmentSnapshot(
                handover_id=record.handover_id,
                equipment_id=eq.equipment_id,
                condition_at_inspection=cond,
                notes=notes
            )
            db.add(snap)
            eq.condition = cond

        kit.location = KitLocation.WITH_CLIENT
        db.commit()

        evt = BookingEvent(
            booking_id=booking_id,
            actor_id=admin_id,
            event_type="CHECKED_OUT",
            reason=f"Kit {kit.name} handed over to client after inspection."
        )
        db.add(evt)
        db.commit()

        return record

    @staticmethod
    def return_kit(
        db: Session,
        booking_id: str,
        admin_id: str,
        inspected_items: List[Dict]
    ) -> HandoverRecord:
        """
        Return Inspection (SDD Section 8.4, 16.7):
        - Compares return inspection against checkout snapshot.
        - Stores snapshots & records differences.
        - If damage/missing found, opens an Incident automatically.
        - Kit location returns to STORAGE, booking moves to COMPLETED.
        """
        b = db.query(Booking).filter(Booking.booking_id == booking_id).first()
        if not b or b.status not in (BookingStatus.CONFIRMED, BookingStatus.AT_RISK):
            raise ValueError("Booking is not checked out")

        kit = db.query(EventKit).filter(EventKit.kit_id == b.kit_id).first()
        record = HandoverRecord(
            booking_id=booking_id,
            kit_id=b.kit_id,
            admin_id=admin_id,
            handover_type=HandoverType.RETURN,
            recorded_at=datetime.now(timezone.utc)
        )
        db.add(record)
        db.commit()

        inspected_map = {item["equipment_id"]: item for item in inspected_items}

        damaged_or_missing = []
        for eq in kit.equipment:
            insp = inspected_map.get(eq.equipment_id)
            cond = insp["condition"] if insp else EquipmentCondition.GOOD
            notes = insp.get("notes") if insp else None

            snap = HandoverEquipmentSnapshot(
                handover_id=record.handover_id,
                equipment_id=eq.equipment_id,
                condition_at_inspection=cond,
                notes=notes
            )
            db.add(snap)
            eq.condition = cond

            if cond in (EquipmentCondition.DAMAGED, EquipmentCondition.MISSING):
                damaged_or_missing.append((eq, cond, notes))

        # Handle incidents if damage found
        for eq, cond, notes in damaged_or_missing:
            IncidentManager.open_incident(
                db, kit_id=kit.kit_id, equipment_id=eq.equipment_id,
                booking_id=booking_id, description=f"Component {eq.type} ({eq.serial_label}) returned in {cond} condition. {notes or ''}",
                reported_by=admin_id
            )

        kit.location = KitLocation.STORAGE
        b.status = BookingStatus.COMPLETED
        db.commit()

        evt = BookingEvent(
            booking_id=booking_id,
            actor_id=admin_id,
            event_type="RETURNED",
            to_status=BookingStatus.COMPLETED,
            reason="Kit returned and inspected."
        )
        db.add(evt)
        db.commit()

        return record


class IncidentManager:
    @staticmethod
    def open_incident(
        db: Session,
        kit_id: str,
        equipment_id: Optional[str],
        booking_id: Optional[str],
        description: str,
        reported_by: Optional[str] = None
    ) -> Incident:
        """
        Incident Management (SDD Section 8.5, 16.8):
        - Opens incident record.
        - Component condition updated. Kit becomes NOT READY automatically.
        - Identifies upcoming bookings on affected kit and sets them to AT_RISK.
        """
        inc = Incident(
            kit_id=kit_id,
            equipment_id=equipment_id,
            booking_id=booking_id,
            description=description,
            status=IncidentStatus.OPEN,
            reported_by=reported_by
        )
        db.add(inc)

        if equipment_id:
            eq = db.query(Equipment).filter(Equipment.equipment_id == equipment_id).first()
            if eq and eq.condition == EquipmentCondition.GOOD:
                eq.condition = EquipmentCondition.DAMAGED

        db.commit()
        db.refresh(inc)

        # Mark upcoming bookings on this kit as AT_RISK
        now = datetime.now(timezone.utc)
        upcoming = db.query(Booking).filter(
            Booking.kit_id == kit_id,
            Booking.status == BookingStatus.CONFIRMED,
            Booking.start_time >= now
        ).all()

        for b in upcoming:
            b.status = BookingStatus.AT_RISK
            evt = BookingEvent(
                booking_id=b.booking_id,
                actor_id=reported_by,
                event_type="MARKED_AT_RISK",
                from_status=BookingStatus.CONFIRMED,
                to_status=BookingStatus.AT_RISK,
                reason=f"Incident {inc.incident_id[:8]} opened on assigned kit"
            )
            db.add(evt)
            NotificationService.notify(
                db, b.client_id, "BOOKING_AT_RISK",
                f"Notice: Equipment issue reported on kit assigned for your booking {b.booking_id[:8]}. Our team is investigating alternatives.",
                booking_id=b.booking_id, incident_id=inc.incident_id
            )

        db.commit()
        return inc

    @staticmethod
    def resolve_incident(
        db: Session,
        incident_id: str,
        resolution_note: str,
        admin_id: str
    ) -> Incident:
        """
        Resolving an Incident (SDD Section 15.3, 15.4):
        - Sets incident status to RESOLVED.
        - Note: Component condition must be updated through an inspection!
        """
        inc = db.query(Incident).filter(Incident.incident_id == incident_id).first()
        if not inc:
            raise ValueError("Incident not found")

        inc.status = IncidentStatus.RESOLVED
        inc.resolution = resolution_note
        inc.resolved_at = datetime.now(timezone.utc)
        db.commit()
        return inc


class WaitlistManager:
    @staticmethod
    def join_waitlist(
        db: Session,
        client_id: str,
        requested_start: datetime,
        requested_end: datetime
    ) -> WaitlistEntry:
        """Joins FIFO Waitlist Queue (SDD Section 8.5, 16.11)"""
        entry = WaitlistEntry(
            client_id=client_id,
            requested_start=requested_start,
            requested_end=requested_end,
            joined_at=datetime.now(timezone.utc),
            status=WaitlistStatus.WAITING
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry

    @staticmethod
    def offer_slot(db: Session, entry_id: str, admin_id: str) -> WaitlistEntry:
        entry = db.query(WaitlistEntry).filter(WaitlistEntry.entry_id == entry_id).first()
        if not entry or entry.status != WaitlistStatus.WAITING:
            raise ValueError("Waitlist entry is not WAITING")

        entry.status = WaitlistStatus.OFFERED
        entry.offered_at = datetime.now(timezone.utc)
        entry.offer_expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        entry.decided_by_admin_id = admin_id
        db.commit()

        NotificationService.notify(
            db, entry.client_id, "WAITLIST_OFFER",
            f"A slot matching your requested interval ({entry.requested_start.strftime('%b %d %H:%M')}) is now available! Accept your offer in the portal."
        )
        return entry

    @staticmethod
    def respond_offer(db: Session, entry_id: str, client_id: str, accept: bool) -> WaitlistEntry:
        entry = db.query(WaitlistEntry).filter(WaitlistEntry.entry_id == entry_id, WaitlistEntry.client_id == client_id).first()
        if not entry or entry.status != WaitlistStatus.OFFERED:
            raise ValueError("Offer not found or not in OFFERED state")

        if accept:
            entry.status = WaitlistStatus.ACCEPTED
            # Create pending booking
            b = BookingManager.create_request(
                db, client_id=client_id,
                start_time=entry.requested_start,
                end_time=entry.requested_end,
                purpose="Created from accepted waitlist offer"
            )
            entry.resulting_booking_id = b.booking_id
        else:
            entry.status = WaitlistStatus.DECLINED

        db.commit()
        return entry
