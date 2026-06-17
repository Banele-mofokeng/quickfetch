from fastapi import APIRouter, Request, BackgroundTasks, Header, HTTPException
from starlette.requests import ClientDisconnect
import json

from app.services.whatsapp import WhatsAppService
from app.services.payment import PaymentService

router = APIRouter()
wa = WhatsAppService()
pay = PaymentService()


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request, bg: BackgroundTasks):
    try:
        data = await request.json()
    except (ClientDisconnect, Exception):
        return {"status": "ok"}
    bg.add_task(wa.handle_webhook, data)
    return {"status": "ok"}


@router.post("/paystack")
async def paystack_webhook(request: Request, bg: BackgroundTasks,
                           x_paystack_signature: str = Header(default="")):
    body = await request.body()
    if not pay.verify_signature(body, x_paystack_signature):
        raise HTTPException(status_code=400, detail="bad signature")
    bg.add_task(pay.handle_webhook, json.loads(body))
    return {"status": "ok"}
