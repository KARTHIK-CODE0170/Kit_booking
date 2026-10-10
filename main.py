from datetime import datetime, timezone
from typing import Optional, List
from fastapi import FastAPI, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from models import (
    Role, BookingStatus, EquipmentCondition, KitLocation, IncidentStatus,
    WaitlistStatus, Booking, EventKit, Equipment, Incident, WaitlistEntry,
    Notification, BookingEvent, HandoverRecord, LateReturnCase
)
from database import init_db, get_db, seed_db, SessionLocal
import schemas
from services import (
    AvailabilityScheduler, BookingManager, HandoverManager,
    IncidentManager, WaitlistManager, NotificationService
)

app = FastAPI(
    title="Event Kit Rental System API",
    description="System Design Document implementation for Event Kit Rental System",
    version="1.0.0"
)

# Startup DB initialization
@app.on_event("startup")
def on_startup():
    init_db()


# ==========================================
# 19. REST API ENDPOINTS
# ==========================================

@app.get("/api/v1/health")
def health():
    return {"status": "ok", "system": "Event Kit Rental System", "time": datetime.now(timezone.utc).isoformat()}


@app.post("/api/v1/auth/login", response_model=schemas.UserResponse)
def login(req: schemas.UserLoginRequest, db: Session = Depends(get_db)):
    from models import User
    u = db.query(User).filter(User.email == req.email).first()
    if not u or u.password_hash != req.password:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return u


# 19.3 Bookings APIs
@app.post("/api/v1/bookings", response_model=schemas.BookingResponse, status_code=201)
def create_booking(req: schemas.BookingCreateRequest, client_id: str = Query("u_client1"), db: Session = Depends(get_db)):
    try:
        b = BookingManager.create_request(
            db, client_id=client_id,
            start_time=req.start_time, end_time=req.end_time, purpose=req.purpose
        )
        return b
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v1/bookings", response_model=List[schemas.BookingResponse])
def list_bookings(client_id: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(Booking)
    if client_id:
        q = q.filter(Booking.client_id == client_id)
    bookings = q.order_by(Booking.created_at.desc()).all()
    
    # attach names
    res = []
    for b in bookings:
        resp = schemas.BookingResponse.from_orm(b)
        if b.client:
            resp.client_name = b.client.name
        if b.kit:
            resp.kit_name = b.kit.name
        res.append(resp)
    return res


@app.get("/api/v1/bookings/{booking_id}", response_model=schemas.BookingResponse)
def get_booking(booking_id: str, db: Session = Depends(get_db)):
    b = db.query(Booking).filter(Booking.booking_id == booking_id).first()
    if not b:
        raise HTTPException(status_code=404, detail="Booking not found")
    resp = schemas.BookingResponse.from_orm(b)
    if b.client:
        resp.client_name = b.client.name
    if b.kit:
        resp.kit_name = b.kit.name
    return resp


@app.post("/api/v1/bookings/{booking_id}/cancel", response_model=schemas.BookingResponse)
def cancel_booking(
    booking_id: str,
    req: schemas.BookingCancelRequest,
    actor_id: str = Query("u_client1"),
    is_admin: bool = Query(False),
    db: Session = Depends(get_db)
):
    try:
        b = BookingManager.cancel_booking(
            db, booking_id=booking_id, actor_id=actor_id, is_admin=is_admin, reason=req.reason
        )
        return b
    except ValueError as e:
        raise HTTPException(status_code=409 if "cutoff" in str(e).lower() else 400, detail=str(e))


@app.get("/api/v1/bookings/{booking_id}/events", response_model=List[schemas.BookingEventResponse])
def get_booking_events(booking_id: str, db: Session = Depends(get_db)):
    evts = db.query(BookingEvent).filter(BookingEvent.booking_id == booking_id).order_by(BookingEvent.created_at).all()
    return evts


# 19.4 Availability & Kits APIs
@app.get("/api/v1/availability")
def check_availability(start: datetime, end: datetime, db: Session = Depends(get_db)):
    eligible_kits = AvailabilityScheduler.find_eligible_kits(db, start, end)
    any_available = any(k["is_eligible"] for k in eligible_kits)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "any_available": any_available,
        "kits": eligible_kits
    }


@app.get("/api/v1/kits", response_model=List[schemas.KitResponse])
def list_kits(db: Session = Depends(get_db)):
    kits = db.query(EventKit).all()
    res = []
    for k in kits:
        kr = schemas.KitResponse(
            kit_id=k.kit_id,
            name=k.name,
            location=k.location,
            is_active=k.is_active,
            is_ready=k.is_ready,
            equipment=[schemas.EquipmentResponse.from_orm(eq) for eq in k.equipment]
        )
        res.append(kr)
    return res


@app.post("/api/v1/kits", response_model=schemas.KitResponse, status_code=201)
def create_kit(req: schemas.KitCreateRequest, db: Session = Depends(get_db)):
    kit = EventKit(name=req.name, location=req.location)
    db.add(kit)
    db.commit()
    db.refresh(kit)
    return kit


@app.patch("/api/v1/equipment/{equipment_id}")
def update_equipment_condition(equipment_id: str, req: schemas.EquipmentUpdateRequest, db: Session = Depends(get_db)):
    eq = db.query(Equipment).filter(Equipment.equipment_id == equipment_id).first()
    if not eq:
        raise HTTPException(status_code=404, detail="Equipment not found")
    eq.condition = req.condition
    eq.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"message": "Equipment condition updated", "equipment_id": equipment_id, "new_condition": req.condition}


# 19.6 Handover APIs
@app.post("/api/v1/bookings/{booking_id}/checkout")
def checkout_booking(booking_id: str, req: schemas.CheckoutRequest, admin_id: str = Query("u_admin"), db: Session = Depends(get_db)):
    try:
        inspected = [item.dict() for item in req.items]
        rec = HandoverManager.checkout(db, booking_id, admin_id, inspected)
        return {"status": "CHECKED_OUT", "handover_id": rec.handover_id, "recorded_at": rec.recorded_at.isoformat()}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/v1/bookings/{booking_id}/return")
def return_booking(booking_id: str, req: schemas.ReturnRequest, admin_id: str = Query("u_admin"), db: Session = Depends(get_db)):
    try:
        inspected = [item.dict() for item in req.items]
        rec = HandoverManager.return_kit(db, booking_id, admin_id, inspected)
        return {"status": "COMPLETED", "handover_id": rec.handover_id, "recorded_at": rec.recorded_at.isoformat()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# 19.7 Incidents APIs
@app.get("/api/v1/incidents", response_model=List[schemas.IncidentResponse])
def list_incidents(db: Session = Depends(get_db)):
    return db.query(Incident).order_by(Incident.created_at.desc()).all()


@app.post("/api/v1/incidents", response_model=schemas.IncidentResponse, status_code=201)
def create_incident(req: schemas.IncidentCreateRequest, admin_id: str = Query("u_admin"), db: Session = Depends(get_db)):
    inc = IncidentManager.open_incident(
        db, kit_id=req.kit_id, equipment_id=req.equipment_id,
        booking_id=req.booking_id, description=req.description, reported_by=admin_id
    )
    return inc


@app.patch("/api/v1/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: str, resolution: str = Query(...), admin_id: str = Query("u_admin"), db: Session = Depends(get_db)):
    try:
        inc = IncidentManager.resolve_incident(db, incident_id, resolution, admin_id)
        return {"message": "Incident resolved", "incident_id": inc.incident_id, "status": inc.status}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# 19.8 Waitlist APIs
@app.post("/api/v1/waitlist", response_model=schemas.WaitlistResponse, status_code=201)
def join_waitlist(req: schemas.WaitlistJoinRequest, client_id: str = Query("u_client1"), db: Session = Depends(get_db)):
    entry = WaitlistManager.join_waitlist(db, client_id, req.requested_start, req.requested_end)
    return entry


@app.get("/api/v1/waitlist", response_model=List[schemas.WaitlistResponse])
def list_waitlist(db: Session = Depends(get_db)):
    entries = db.query(WaitlistEntry).order_by(WaitlistEntry.joined_at).all()
    res = []
    for e in entries:
        r = schemas.WaitlistResponse.from_orm(e)
        if e.client:
            r.client_name = e.client.name
        res.append(r)
    return res


@app.post("/api/v1/waitlist/{entry_id}/offer")
def offer_waitlist(entry_id: str, admin_id: str = Query("u_admin"), db: Session = Depends(get_db)):
    try:
        e = WaitlistManager.offer_slot(db, entry_id, admin_id)
        return {"message": "Offer sent", "entry_id": e.entry_id, "status": e.status}
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))


@app.post("/api/v1/waitlist/{entry_id}/respond")
def respond_waitlist(entry_id: str, req: schemas.WaitlistRespondRequest, client_id: str = Query("u_client1"), db: Session = Depends(get_db)):
    try:
        e = WaitlistManager.respond_offer(db, entry_id, client_id, req.accept)
        return {"message": "Response recorded", "entry_id": e.entry_id, "status": e.status, "resulting_booking_id": e.resulting_booking_id}
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))


# 19.9 Notifications APIs
@app.get("/api/v1/notifications", response_model=List[schemas.NotificationResponse])
def list_notifications(recipient_id: str = Query("u_client1"), db: Session = Depends(get_db)):
    return db.query(Notification).filter(Notification.recipient_id == recipient_id).order_by(Notification.created_at.desc()).all()


@app.post("/api/v1/notifications/{notification_id}/read")
def mark_notification_read(notification_id: str, db: Session = Depends(get_db)):
    n = db.query(Notification).filter(Notification.notification_id == notification_id).first()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    n.is_read = True
    n.read_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "read", "notification_id": notification_id}


# 19.10 Admin Actions APIs
@app.post("/api/v1/admin/bookings/{booking_id}/assign")
def admin_assign_kit(booking_id: str, req: schemas.BookingAssignKitRequest, admin_id: str = Query("u_admin"), db: Session = Depends(get_db)):
    try:
        b = BookingManager.assign_kit(db, booking_id, req.kit_id, admin_id)
        return b
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/admin/bookings/{booking_id}/confirm")
def admin_confirm_booking(booking_id: str, req: schemas.BookingConfirmRequest, admin_id: str = Query("u_admin"), db: Session = Depends(get_db)):
    try:
        b = BookingManager.confirm_booking(
            db, booking_id=booking_id, kit_id=req.kit_id, admin_id=admin_id, expected_version=req.expected_version
        )
        return b
    except ValueError as e:
        raise HTTPException(status_code=409 if "refused" in str(e).lower() or "conflict" in str(e).lower() else 400, detail=str(e))


@app.post("/api/v1/admin/bookings/{booking_id}/reject")
def admin_reject_booking(booking_id: str, req: schemas.BookingRejectRequest, admin_id: str = Query("u_admin"), db: Session = Depends(get_db)):
    try:
        b = BookingManager.reject_booking(db, booking_id, admin_id, req.reason)
        return b
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/admin/reset-data")
def reset_demo_data(db: Session = Depends(get_db)):
    from models import Base
    from database import engine
    Base.metadata.drop_all(bind=engine)
    init_db()
    return {"message": "Demo data reset successfully"}


# ==========================================
# 2. EMBEDDED DASHBOARD WEB INTERFACE (HTML/JS/CSS)
# ==========================================

@app.get("/", response_class=HTMLResponse)
def index_web_app():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Event Kit Rental System – System Design Showcase</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <script src="https://unpkg.com/lucide@latest"></script>
  <style>
    :root {
      --bg-gradient: radial-gradient(circle at 10% 20%, rgba(18, 24, 38, 1) 0%, rgba(10, 14, 23, 1) 90%);
      --surface: rgba(22, 31, 49, 0.7);
      --surface-hover: rgba(30, 42, 66, 0.8);
      --surface-border: rgba(255, 255, 255, 0.08);
      --primary: #6366f1;
      --primary-hover: #4f46e5;
      --primary-glow: rgba(99, 102, 241, 0.35);
      --accent: #38bdf8;
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --radius-lg: 16px;
      --radius-md: 10px;
      --font-main: 'Plus Jakarta Sans', sans-serif;
      --font-mono: 'JetBrains Mono', monospace;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: var(--font-main);
      background: #0a0e17;
      background-image: var(--bg-gradient);
      color: var(--text-main);
      min-height: 100vh;
      overflow-x: hidden;
    }

    .glass {
      background: var(--surface);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      border: 1px solid var(--surface-border);
      border-radius: var(--radius-lg);
    }

    header {
      padding: 16px 32px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid var(--surface-border);
      position: sticky;
      top: 0;
      z-index: 100;
      background: rgba(10, 14, 23, 0.85);
      backdrop-filter: blur(12px);
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      font-weight: 800;
      font-size: 1.25rem;
      letter-spacing: -0.02em;
    }
    .brand-icon {
      width: 40px;
      height: 40px;
      border-radius: 12px;
      background: linear-gradient(135deg, var(--primary), var(--accent));
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 0 20px var(--primary-glow);
    }

    .role-switcher {
      display: flex;
      background: rgba(0, 0, 0, 0.3);
      padding: 4px;
      border-radius: 12px;
      border: 1px solid var(--surface-border);
    }
    .role-btn {
      padding: 8px 18px;
      border-radius: 8px;
      border: none;
      background: transparent;
      color: var(--text-muted);
      font-weight: 600;
      font-size: 0.85rem;
      cursor: pointer;
      transition: all 0.2s;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .role-btn.active {
      background: var(--primary);
      color: white;
      box-shadow: 0 0 12px var(--primary-glow);
    }

    .user-info {
      display: flex;
      align-items: center;
      gap: 16px;
    }

    .notif-bell {
      position: relative;
      cursor: pointer;
      padding: 8px;
      border-radius: 50%;
      background: var(--surface);
      border: 1px solid var(--surface-border);
    }
    .notif-badge {
      position: absolute;
      top: -4px; right: -4px;
      background: var(--danger);
      color: white;
      font-size: 0.7rem;
      font-weight: 700;
      width: 18px; height: 18px;
      border-radius: 50%;
      display: flex; align-items: center; justify-content: center;
    }

    .container {
      max-width: 1400px;
      margin: 0 auto;
      padding: 24px;
    }

    .nav-tabs {
      display: flex;
      gap: 12px;
      margin-bottom: 24px;
      border-bottom: 1px solid var(--surface-border);
      padding-bottom: 12px;
    }
    .tab-btn {
      padding: 10px 20px;
      border-radius: 10px;
      border: 1px solid transparent;
      background: transparent;
      color: var(--text-muted);
      font-weight: 600;
      font-size: 0.95rem;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 8px;
      transition: all 0.2s;
    }
    .tab-btn:hover { background: var(--surface-hover); color: white; }
    .tab-btn.active {
      background: var(--surface);
      border-color: var(--primary);
      color: white;
    }

    .grid-dashboard {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 20px;
      margin-bottom: 28px;
    }
    .stat-card {
      padding: 20px;
      display: flex;
      align-items: center;
      gap: 16px;
    }
    .stat-icon {
      width: 48px; height: 48px;
      border-radius: 14px;
      display: flex; align-items: center; justify-content: center;
      font-size: 1.4rem;
    }

    .badge {
      padding: 4px 10px;
      border-radius: 6px;
      font-size: 0.75rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }
    .badge-PENDING { background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3); }
    .badge-CONFIRMED { background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }
    .badge-AT_RISK { background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.4); }
    .badge-COMPLETED { background: rgba(99, 102, 241, 0.15); color: #818cf8; border: 1px solid rgba(99, 102, 241, 0.3); }
    .badge-REJECTED { background: rgba(148, 163, 184, 0.15); color: #cbd5e1; border: 1px solid rgba(148, 163, 184, 0.3); }
    .badge-CANCELLED { background: rgba(239, 68, 68, 0.1); color: #fca5a5; }

    .btn {
      padding: 8px 16px;
      border-radius: 8px;
      border: none;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.2s;
      font-size: 0.85rem;
    }
    .btn-primary { background: var(--primary); color: white; }
    .btn-primary:hover { background: var(--primary-hover); box-shadow: 0 0 15px var(--primary-glow); }
    .btn-success { background: var(--success); color: white; }
    .btn-danger { background: var(--danger); color: white; }
    .btn-outline { background: transparent; border: 1px solid var(--surface-border); color: var(--text-main); }
    .btn-outline:hover { background: var(--surface-hover); }

    table {
      width: 100%;
      border-collapse: collapse;
      margin-top: 12px;
    }
    th, td {
      padding: 14px 16px;
      text-align: left;
      border-bottom: 1px solid var(--surface-border);
      font-size: 0.9rem;
    }
    th {
      color: var(--text-muted);
      font-weight: 600;
      font-size: 0.8rem;
      text-transform: uppercase;
    }
    tr:hover td { background: rgba(255, 255, 255, 0.02); }

    .modal-backdrop {
      position: fixed;
      top: 0; left: 0; width: 100%; height: 100%;
      background: rgba(0, 0, 0, 0.7);
      backdrop-filter: blur(8px);
      display: flex; justify-content: center; align-items: center;
      z-index: 1000;
      opacity: 0; pointer-events: none; transition: all 0.25s;
    }
    .modal-backdrop.show { opacity: 1; pointer-events: auto; }
    .modal {
      width: 90%; max-width: 600px;
      max-height: 90vh; overflow-y: auto;
      padding: 28px;
      position: relative;
    }

    .timeline-bar {
      display: flex;
      height: 32px;
      background: rgba(0,0,0,0.4);
      border-radius: 8px;
      overflow: hidden;
      margin: 16px 0;
      border: 1px solid var(--surface-border);
    }
    .timeline-segment {
      display: flex; align-items: center; justify-content: center;
      font-size: 0.75rem; font-weight: 700; color: white;
    }
    .gap-indicator {
      background: repeating-linear-gradient(45deg, #f59e0b, #f59e0b 10px, #b45309 10px, #b45309 20px);
    }

    .form-group {
      margin-bottom: 16px;
    }
    .form-group label {
      display: block; font-size: 0.85rem; font-weight: 600; color: var(--text-muted); margin-bottom: 6px;
    }
    .form-control {
      width: 100%; padding: 10px 14px;
      border-radius: 8px;
      border: 1px solid var(--surface-border);
      background: rgba(10, 14, 23, 0.6);
      color: white; font-family: var(--font-main);
    }
    .form-control:focus { border-color: var(--primary); outline: none; }
  </style>
</head>
<body>

  <header>
    <div class="brand">
      <div class="brand-icon"><i data-lucide="package-check"></i></div>
      <div>Event Kit Rental System <span style="font-size: 0.75rem; color: var(--accent); font-weight: 500; display: block;">System Design Document (SDD v0.1)</span></div>
    </div>

    <div class="role-switcher">
      <button class="role-btn active" id="btn-role-client" onclick="switchRole('CLIENT')">
        <i data-lucide="user"></i> Client Portal (Asha R)
      </button>
      <button class="role-btn" id="btn-role-admin" onclick="switchRole('ADMIN')">
        <i data-lucide="shield-check"></i> Admin Dashboard (Surya)
      </button>
    </div>

    <div class="user-info">
      <button class="btn btn-outline" onclick="resetDemoData()"><i data-lucide="rotate-ccw"></i> Reset Demo</button>
      <div class="notif-bell" onclick="toggleNotifications()">
        <i data-lucide="bell"></i>
        <div class="notif-badge" id="notif-count">0</div>
      </div>
    </div>
  </header>

  <div class="container">

    <!-- DASHBOARD SUMMARY CARDS -->
    <div class="grid-dashboard">
      <div class="glass stat-card">
        <div class="stat-icon" style="background: rgba(99, 102, 241, 0.2); color: var(--primary);"><i data-lucide="layers"></i></div>
        <div>
          <div style="font-size: 0.85rem; color: var(--text-muted);">Active Event Kits</div>
          <div style="font-size: 1.6rem; font-weight: 800;" id="stat-kits">-</div>
        </div>
      </div>
      <div class="glass stat-card">
        <div class="stat-icon" style="background: rgba(245, 158, 11, 0.2); color: var(--warning);"><i data-lucide="clock"></i></div>
        <div>
          <div style="font-size: 0.85rem; color: var(--text-muted);">Pending Requests</div>
          <div style="font-size: 1.6rem; font-weight: 800;" id="stat-pending">-</div>
        </div>
      </div>
      <div class="glass stat-card">
        <div class="stat-icon" style="background: rgba(16, 185, 129, 0.2); color: var(--success);"><i data-lucide="check-circle"></i></div>
        <div>
          <div style="font-size: 0.85rem; color: var(--text-muted);">Confirmed Bookings</div>
          <div style="font-size: 1.6rem; font-weight: 800;" id="stat-confirmed">-</div>
        </div>
      </div>
      <div class="glass stat-card">
        <div class="stat-icon" style="background: rgba(239, 68, 68, 0.2); color: var(--danger);"><i data-lucide="alert-triangle"></i></div>
        <div>
          <div style="font-size: 0.85rem; color: var(--text-muted);">Incidents / At Risk</div>
          <div style="font-size: 1.6rem; font-weight: 800;" id="stat-incidents">-</div>
        </div>
      </div>
    </div>

    <!-- NAVIGATION TABS -->
    <div class="nav-tabs">
      <button class="tab-btn active" onclick="showTab('bookings')"><i data-lucide="calendar"></i> Bookings & History</button>
      <button class="tab-btn" onclick="showTab('kits')"><i data-lucide="box"></i> Kits & Component Conditions</button>
      <button class="tab-btn" onclick="showTab('incidents')"><i data-lucide="alert-circle"></i> Incident Management</button>
      <button class="tab-btn" onclick="showTab('waitlist')"><i data-lucide="users"></i> FIFO Waitlist Queue</button>
      <button class="tab-btn" onclick="showTab('rules')"><i data-lucide="book-open"></i> System Design Rules</button>
    </div>

    <!-- TAB CONTENTS -->
    <!-- 1. BOOKINGS TAB -->
    <div id="tab-bookings" class="tab-pane">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
        <h2 style="font-size: 1.3rem; font-weight: 700;" id="bookings-title">Booking Requests & Reservations</h2>
        <button class="btn btn-primary" id="btn-new-booking" onclick="openNewBookingModal()"><i data-lucide="plus-circle"></i> New Booking Request</button>
      </div>

      <div class="glass" style="padding: 12px; overflow-x: auto;">
        <table>
          <thead>
            <tr>
              <th>Booking ID</th>
              <th>Client</th>
              <th>Assigned Kit</th>
              <th>Start Time (UTC)</th>
              <th>End Time (UTC)</th>
              <th>Protected Range (End + 15m)</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="bookings-table-body">
            <tr><td colspan="8" style="text-align:center;">Loading bookings...</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 2. KITS & COMPONENTS TAB -->
    <div id="tab-kits" class="tab-pane" style="display: none;">
      <h2 style="font-size: 1.3rem; font-weight: 700; margin-bottom: 16px;">Event Kits & Component Readiness</h2>
      <div id="kits-container" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 20px;">
      </div>
    </div>

    <!-- 3. INCIDENTS TAB -->
    <div id="tab-incidents" class="tab-pane" style="display: none;">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
        <h2 style="font-size: 1.3rem; font-weight: 700;">Equipment Incidents & Recovery</h2>
        <button class="btn btn-danger" onclick="openNewIncidentModal()"><i data-lucide="alert-octagon"></i> Report Equipment Damage</button>
      </div>
      <div class="glass" style="padding: 12px;">
        <table>
          <thead>
            <tr>
              <th>Incident ID</th>
              <th>Kit</th>
              <th>Equipment</th>
              <th>Description</th>
              <th>Status</th>
              <th>Reported At</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="incidents-table-body">
          </tbody>
        </table>
      </div>
    </div>

    <!-- 4. WAITLIST TAB -->
    <div id="tab-waitlist" class="tab-pane" style="display: none;">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
        <h2 style="font-size: 1.3rem; font-weight: 700;">FIFO Waitlist Queue</h2>
        <button class="btn btn-primary" onclick="openWaitlistModal()"><i data-lucide="user-plus"></i> Join Waitlist</button>
      </div>
      <div class="glass" style="padding: 12px;">
        <table>
          <thead>
            <tr>
              <th>Entry ID</th>
              <th>Client</th>
              <th>Requested Interval</th>
              <th>Joined At (FIFO Order)</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="waitlist-table-body">
          </tbody>
        </table>
      </div>
    </div>

    <!-- 5. RULES & ARCHITECTURE TAB -->
    <div id="tab-rules" class="tab-pane" style="display: none;">
      <div class="glass" style="padding: 24px;">
        <h2 style="font-size: 1.3rem; font-weight: 700; margin-bottom: 16px; color: var(--accent);">Key Enforced SDD Rules & Architectural Highlights</h2>
        
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px;">
          <div style="background: rgba(0,0,0,0.3); padding: 16px; border-radius: 12px; border-left: 4px solid var(--primary);">
            <h4 style="color: white; margin-bottom: 8px;">1. Mandatory 15-Minute Gap Rule (SDD §17.2)</h4>
            <p style="font-size: 0.85rem; color: var(--text-muted);">Each blocking booking on a kit occupies its protected range <code>[start_time, end_time + 15 min)</code>. Consecutive bookings must be at least 15 mins apart for inspection.</p>
          </div>
          <div style="background: rgba(0,0,0,0.3); padding: 16px; border-radius: 12px; border-left: 4px solid var(--warning);">
            <h4 style="color: white; margin-bottom: 8px;">2. Two-Hour Cancellation Cutoff (SDD §16.5)</h4>
            <p style="font-size: 0.85rem; color: var(--text-muted);">Clients may cancel up to <code>start_time - 2 hours</code>. Past cutoff, client cancellations are blocked. Admin override requires audited reason.</p>
          </div>
          <div style="background: rgba(0,0,0,0.3); padding: 16px; border-radius: 12px; border-left: 4px solid var(--danger);">
            <h4 style="color: white; margin-bottom: 8px;">3. Derived Kit Readiness (SDD §15.1)</h4>
            <p style="font-size: 0.85rem; color: var(--text-muted);">Readiness is NEVER manually set. A kit is ready ONLY if all required components are in <code>GOOD</code> condition. Any damaged/missing item blocks handover.</p>
          </div>
          <div style="background: rgba(0,0,0,0.3); padding: 16px; border-radius: 12px; border-left: 4px solid var(--success);">
            <h4 style="color: white; margin-bottom: 8px;">4. Atomic Confirmation & Kit Lock (SDD §13.4)</h4>
            <p style="font-size: 0.85rem; color: var(--text-muted);">Re-checks availability, readiness, and gap under database transaction & row locking before confirming to guarantee zero double bookings.</p>
          </div>
        </div>
      </div>
    </div>

  </div>

  <!-- MODALS -->
  <!-- 1. NEW BOOKING MODAL -->
  <div class="modal-backdrop" id="modal-booking">
    <div class="glass modal">
      <h3 style="margin-bottom: 16px;">Request Event Kit Booking</h3>
      <form id="form-booking" onsubmit="handleCreateBooking(event)">
        <div class="form-group">
          <label>Start Time (UTC)</label>
          <input type="datetime-local" class="form-control" id="booking-start" required>
        </div>
        <div class="form-group">
          <label>End Time (UTC)</label>
          <input type="datetime-local" class="form-control" id="booking-end" required>
        </div>
        <div class="form-group">
          <label>Event Purpose / Notes</label>
          <input type="text" class="form-control" id="booking-purpose" placeholder="e.g. Club Annual Fest Video Recording">
        </div>
        <div style="display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px;">
          <button type="button" class="btn btn-outline" onclick="closeModal('modal-booking')">Cancel</button>
          <button type="submit" class="btn btn-primary">Submit Request</button>
        </div>
      </form>
    </div>
  </div>

  <!-- 2. CHECKOUT / RETURN INSPECTION MODAL -->
  <div class="modal-backdrop" id="modal-inspection">
    <div class="glass modal">
      <h3 style="margin-bottom: 8px;" id="inspection-title">Component Inspection Checklist</h3>
      <p style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 16px;" id="inspection-subtitle"></p>
      
      <div id="inspection-checklist" style="margin-bottom: 20px;"></div>

      <div style="display: flex; justify-content: flex-end; gap: 10px;">
        <button type="button" class="btn btn-outline" onclick="closeModal('modal-inspection')">Cancel</button>
        <button type="button" class="btn btn-success" id="btn-submit-inspection">Complete Inspection</button>
      </div>
    </div>
  </div>

  <!-- 3. NOTIFICATION DRAWER -->
  <div class="modal-backdrop" id="modal-notif">
    <div class="glass modal" style="max-width: 450px;">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
        <h3>In-App Notifications</h3>
        <button class="btn btn-outline" onclick="closeModal('modal-notif')">Close</button>
      </div>
      <div id="notif-list" style="display: flex; flex-direction: column; gap: 10px;">
      </div>
    </div>
  </div>

  <script>
    let currentRole = 'CLIENT';
    let currentUserId = 'u_client1';
    let allKits = [];
    let allBookings = [];

    document.addEventListener('DOMContentLoaded', () => {
      lucide.createIcons();
      initDates();
      loadData();
    });

    function initDates() {
      const now = new Date();
      now.setHours(now.getHours() + 4);
      document.getElementById('booking-start').value = now.toISOString().slice(0, 16);
      const end = new Date(now.getTime() + 3 * 3600 * 1000);
      document.getElementById('booking-end').value = end.toISOString().slice(0, 16);
    }

    function switchRole(role) {
      currentRole = role;
      currentUserId = role === 'ADMIN' ? 'u_admin' : 'u_client1';
      document.getElementById('btn-role-client').classList.toggle('active', role === 'CLIENT');
      document.getElementById('btn-role-admin').classList.toggle('active', role === 'ADMIN');
      document.getElementById('btn-new-booking').style.display = role === 'CLIENT' ? 'inline-flex' : 'none';
      document.getElementById('bookings-title').innerText = role === 'ADMIN' ? 'All Booking Requests (Admin View)' : 'My Booking Requests & History';
      loadData();
    }

    function showTab(tabName) {
      document.querySelectorAll('.tab-pane').forEach(el => el.style.display = 'none');
      document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
      document.getElementById('tab-' + tabName).style.display = 'block';
      event.currentTarget.classList.add('active');
    }

    async function loadData() {
      await Promise.all([loadBookings(), loadKits(), loadIncidents(), loadWaitlist(), loadNotifications()]);
    }

    async function loadBookings() {
      const url = currentRole === 'CLIENT' ? `/api/v1/bookings?client_id=${currentUserId}` : '/api/v1/bookings';
      const res = await fetch(url);
      allBookings = await res.json();
      
      const tbody = document.getElementById('bookings-table-body');
      tbody.innerHTML = '';

      let countPending = 0, countConfirmed = 0;
      allBookings.forEach(b => {
        if (b.status === 'PENDING') countPending++;
        if (b.status === 'CONFIRMED') countConfirmed++;

        const tr = document.createElement('tr');
        const st = new Date(b.start_time).toUTCString().slice(5, 22);
        const et = new Date(b.end_time).toUTCString().slice(5, 22);
        const ht = new Date(b.held_until).toUTCString().slice(5, 22);

        let actionBtns = '';
        if (currentRole === 'ADMIN' && b.status === 'PENDING') {
          actionBtns += `<button class="btn btn-primary" onclick="confirmBooking('${b.booking_id}')">Confirm</button> `;
          actionBtns += `<button class="btn btn-danger" onclick="rejectBooking('${b.booking_id}')">Reject</button>`;
        } else if (currentRole === 'ADMIN' && b.status === 'CONFIRMED') {
          actionBtns += `<button class="btn btn-success" onclick="openInspectionModal('${b.booking_id}', 'CHECKOUT')">Checkout</button> `;
          actionBtns += `<button class="btn btn-outline" onclick="openInspectionModal('${b.booking_id}', 'RETURN')">Return</button>`;
        } else if (b.status === 'CONFIRMED' || b.status === 'PENDING') {
          actionBtns += `<button class="btn btn-outline" style="color: var(--danger);" onclick="cancelBooking('${b.booking_id}')">Cancel</button>`;
        }

        tr.innerHTML = `
          <td style="font-family: var(--font-mono); font-weight: 600;">${b.booking_id.slice(0,8)}</td>
          <td>${b.client_name || b.client_id}</td>
          <td>${b.kit_name || (b.kit_id ? b.kit_id : '<span style="color:var(--text-muted);">(Unassigned)</span>')}</td>
          <td>${st}</td>
          <td>${et}</td>
          <td style="color: var(--warning); font-size: 0.85rem;">${ht}</td>
          <td><span class="badge badge-${b.status}">${b.status}</span></td>
          <td>${actionBtns}</td>
        `;
        tbody.appendChild(tr);
      });

      document.getElementById('stat-pending').innerText = countPending;
      document.getElementById('stat-confirmed').innerText = countConfirmed;
      lucide.createIcons();
    }

    async function loadKits() {
      const res = await fetch('/api/v1/kits');
      allKits = await res.json();
      document.getElementById('stat-kits').innerText = allKits.length;

      const container = document.getElementById('kits-container');
      container.innerHTML = '';

      allKits.forEach(k => {
        const div = document.createElement('div');
        div.className = 'glass';
        div.style.padding = '20px';

        let eqListHtml = k.equipment.map(eq => `
          <div style="display:flex; justify-content:space-between; align-items:center; padding: 6px 0; border-bottom:1px solid rgba(255,255,255,0.05); font-size:0.85rem;">
            <span>${eq.type} (${eq.serial_label || 'EQ'})</span>
            <span class="badge badge-${eq.condition === 'GOOD' ? 'CONFIRMED' : 'AT_RISK'}">${eq.condition}</span>
          </div>
        `).join('');

        div.innerHTML = `
          <div style="display:flex; justify-size:space-between; align-items:center; margin-bottom:12px;">
            <h3 style="font-size:1.1rem; font-weight:700;">${k.name}</h3>
            <span class="badge badge-${k.is_ready ? 'CONFIRMED' : 'AT_RISK'}">${k.is_ready ? 'READY' : 'NOT READY'}</span>
          </div>
          <div style="font-size:0.85rem; color:var(--text-muted); margin-bottom:12px;">Location: <strong>${k.location}</strong></div>
          <div style="margin-top:10px;">
            <div style="font-size:0.75rem; color:var(--text-muted); text-transform:uppercase; margin-bottom:6px;">Components:</div>
            ${eqListHtml}
          </div>
        `;
        container.appendChild(div);
      });
      lucide.createIcons();
    }

    async function loadIncidents() {
      const res = await fetch('/api/v1/incidents');
      const incs = await res.json();
      document.getElementById('stat-incidents').innerText = incs.length;

      const tbody = document.getElementById('incidents-table-body');
      tbody.innerHTML = '';
      incs.forEach(inc => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td style="font-family: var(--font-mono);">${inc.incident_id.slice(0,8)}</td>
          <td>${inc.kit_id}</td>
          <td>${inc.equipment_id || 'Whole Kit'}</td>
          <td>${inc.description}</td>
          <td><span class="badge badge-${inc.status === 'RESOLVED' ? 'CONFIRMED' : 'AT_RISK'}">${inc.status}</span></td>
          <td>${new Date(inc.created_at).toUTCString().slice(5, 22)}</td>
          <td>${inc.status === 'OPEN' && currentRole === 'ADMIN' ? `<button class="btn btn-success" onclick="resolveIncident('${inc.incident_id}')">Resolve</button>` : ''}</td>
        `;
        tbody.appendChild(tr);
      });
    }

    async function loadWaitlist() {
      const res = await fetch('/api/v1/waitlist');
      const entries = await res.json();
      const tbody = document.getElementById('waitlist-table-body');
      tbody.innerHTML = '';
      entries.forEach(e => {
        const tr = document.createElement('tr');
        let action = '';
        if (currentRole === 'ADMIN' && e.status === 'WAITING') {
          action = `<button class="btn btn-primary" onclick="offerWaitlist('${e.entry_id}')">Offer Slot</button>`;
        } else if (currentRole === 'CLIENT' && e.status === 'OFFERED') {
          action = `<button class="btn btn-success" onclick="respondWaitlist('${e.entry_id}', true)">Accept</button> <button class="btn btn-danger" onclick="respondWaitlist('${e.entry_id}', false)">Decline</button>`;
        }
        tr.innerHTML = `
          <td style="font-family: var(--font-mono);">${e.entry_id.slice(0,8)}</td>
          <td>${e.client_name || e.client_id}</td>
          <td>${new Date(e.requested_start).toUTCString().slice(5, 22)}</td>
          <td>${new Date(e.joined_at).toUTCString().slice(5, 22)}</td>
          <td><span class="badge badge-${e.status === 'ACCEPTED' ? 'CONFIRMED' : 'PENDING'}">${e.status}</span></td>
          <td>${action}</td>
        `;
        tbody.appendChild(tr);
      });
    }

    async function loadNotifications() {
      const res = await fetch(`/api/v1/notifications?recipient_id=${currentUserId}`);
      const notifs = await res.json();
      const unread = notifs.filter(n => !n.is_read).length;
      document.getElementById('notif-count').innerText = unread;

      const list = document.getElementById('notif-list');
      list.innerHTML = '';
      notifs.forEach(n => {
        const div = document.createElement('div');
        div.style.cssText = `padding: 12px; background: rgba(0,0,0,0.4); border-radius: 8px; border-left: 4px solid var(--primary); font-size: 0.85rem;`;
        div.innerHTML = `<div><strong>${n.type}</strong></div><div>${n.message}</div><div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 4px;">${new Date(n.created_at).toUTCString()}</div>`;
        list.appendChild(div);
      });
    }

    // HANDLERS
    function openNewBookingModal() { document.getElementById('modal-booking').classList.add('show'); }
    function closeModal(id) { document.getElementById(id).classList.remove('show'); }
    function toggleNotifications() { document.getElementById('modal-notif').classList.toggle('show'); }

    async function handleCreateBooking(e) {
      e.preventDefault();
      const start = new Date(document.getElementById('booking-start').value).toISOString();
      const end = new Date(document.getElementById('booking-end').value).toISOString();
      const purpose = document.getElementById('booking-purpose').value;

      const res = await fetch(`/api/v1/bookings?client_id=${currentUserId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ start_time: start, end_time: end, purpose: purpose })
      });
      if (res.ok) {
        closeModal('modal-booking');
        loadData();
      } else {
        const err = await res.json();
        alert('Booking failed: ' + (err.detail || 'Error'));
      }
    }

    async function confirmBooking(id) {
      const kitId = prompt('Assign Kit ID for confirmation (e.g. k_01, k_02, k_03):', 'k_01');
      if (!kitId) return;

      const res = await fetch(`/api/v1/admin/bookings/${id}/confirm?admin_id=u_admin`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kit_id: kitId })
      });
      if (res.ok) {
        loadData();
      } else {
        const err = await res.json();
        alert('Confirmation failed: ' + (err.detail || 'Conflict'));
      }
    }

    async function rejectBooking(id) {
      const reason = prompt('Enter rejection reason:', 'Slot unavailable');
      if (!reason) return;
      await fetch(`/api/v1/admin/bookings/${id}/reject?admin_id=u_admin`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: reason })
      });
      loadData();
    }

    async function cancelBooking(id) {
      const isAdmin = currentRole === 'ADMIN';
      const res = await fetch(`/api/v1/bookings/${id}/cancel?actor_id=${currentUserId}&is_admin=${isAdmin}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'Cancelled by user' })
      });
      if (res.ok) {
        loadData();
      } else {
        const err = await res.json();
        alert('Cancellation Error: ' + err.detail);
      }
    }

    async function openInspectionModal(bookingId, type) {
      const b = allBookings.find(x => x.booking_id === bookingId);
      const kit = allKits.find(x => x.kit_id === b.kit_id);
      document.getElementById('inspection-title').innerText = `${type} Inspection – ${kit ? kit.name : ''}`;
      document.getElementById('inspection-subtitle').innerText = `Inspect each component before finalizing ${type.toLowerCase()}. Required items MUST be in GOOD condition.`;

      const container = document.getElementById('inspection-checklist');
      container.innerHTML = '';

      kit.equipment.forEach(eq => {
        const div = document.createElement('div');
        div.style.cssText = `margin-bottom: 12px; padding: 10px; background: rgba(0,0,0,0.3); border-radius: 8px; font-size: 0.85rem;`;
        div.innerHTML = `
          <div style="font-weight:600; margin-bottom: 6px;">${eq.type} (${eq.serial_label || 'EQ'}) ${eq.is_required ? '<span style="color:var(--danger)">*Required</span>' : ''}</div>
          <select class="form-control eq-cond-select" data-eq-id="${eq.equipment_id}">
            <option value="GOOD" ${eq.condition === 'GOOD' ? 'selected' : ''}>GOOD</option>
            <option value="DAMAGED" ${eq.condition === 'DAMAGED' ? 'selected' : ''}>DAMAGED</option>
            <option value="MISSING" ${eq.condition === 'MISSING' ? 'selected' : ''}>MISSING</option>
          </select>
        `;
        container.appendChild(div);
      });

      document.getElementById('btn-submit-inspection').onclick = () => submitInspection(bookingId, type);
      document.getElementById('modal-inspection').classList.add('show');
    }

    async function submitInspection(bookingId, type) {
      const items = [];
      document.querySelectorAll('.eq-cond-select').forEach(sel => {
        items.push({ equipment_id: sel.getAttribute('data-eq-id'), condition: sel.value });
      });

      const endpoint = type === 'CHECKOUT' ? `/api/v1/bookings/${bookingId}/checkout` : `/api/v1/bookings/${bookingId}/return`;
      const res = await fetch(endpoint + '?admin_id=u_admin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items: items })
      });

      if (res.ok) {
        closeModal('modal-inspection');
        loadData();
      } else {
        const err = await res.json();
        alert('Inspection Failed: ' + err.detail);
      }
    }

    async function resolveIncident(id) {
      const note = prompt('Enter resolution details (e.g. Component replaced & re-inspected):', 'Replaced microphone');
      if (!note) return;
      await fetch(`/api/v1/incidents/${id}/resolve?resolution=${encodeURIComponent(note)}&admin_id=u_admin`, { method: 'PATCH' });
      loadData();
    }

    async function resetDemoData() {
      if (confirm('Reset demo database to fresh initial state?')) {
        await fetch('/api/v1/admin/reset-data', { method: 'POST' });
        loadData();
      }
    }
  </script>
</body>
</html>
"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
