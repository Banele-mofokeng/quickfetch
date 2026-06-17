# QuickFetch

WhatsApp-based delivery software for a **single operator running their own fleet**. Customers
order over WhatsApp, a driver buys the goods on their behalf (proxy), and the customer tracks
the order and gets notifications over WhatsApp. No shop partnerships, no marketplace.

This is **not** an Uber Eats / Mr D style marketplace. There is one operator. The drivers are
theirs. The software's job is communication, dispatch, tracking, and getting the money right.

## The money model (the important part)

Because the driver buys goods before the customer has paid for them, the rule is **money is
secured before the driver spends** — with one pragmatic exception for small orders:

| Order budget            | Customer options                  | When the driver buys        |
|-------------------------|-----------------------------------|-----------------------------|
| **≤ R200** (`CASH_THRESHOLD`) | Pay now (Paystack) **or** cash on delivery | Cash: dispatched immediately. Prepay: after payment confirmed. |
| **> R200**              | Prepay only — no cash option offered | After payment is confirmed  |

So the operator never fronts more than the cash threshold in unsecured cash. Set
`CASH_THRESHOLD` and `DELIVERY_FEE` in `.env` to the operator's risk appetite.

**Over budget at the shop.** The driver reports the till total with `bought <amount>`. If it
exceeds the secured budget, the order pauses (`AWAITING_APPROVAL`) and the customer is asked:
- *Prepay order* → a top-up Paystack link for the difference, or cancel.
- *Cash order* → reply YES to approve (pays the higher amount on delivery) or NO to cancel.

The driver doesn't complete the purchase until it's resolved.

**Reconciliation.** On a prepay order, if the driver spent less than the budget, the difference
is automatically refunded. Cash orders settle in cash on delivery (goods + fee).

## Order lifecycle

```
PREPAY:  PENDING_PAYMENT --(paid)--> QUEUED --> ASSIGNED --> EN_ROUTE_TO_SHOP --> AT_SHOP
CASH:                                QUEUED --> ASSIGNED --> EN_ROUTE_TO_SHOP --> AT_SHOP
                                                                                   |
            (over budget) AWAITING_APPROVAL <----------------------------- bought <amount>
                                                                                   |
                                              PURCHASED --> EN_ROUTE_TO_CUSTOMER --> DELIVERED --> COMPLETED
```

Dispatch only fires once an order is `QUEUED` — which for prepay means *after* the Paystack
webhook confirms payment, and for cash means immediately.

## WhatsApp commands

**Customer:** `order` to start, then follow the prompts (shop → items + budget → address →
`PAY`/`CASH`). Replies `YES`/`NO` to approve an over-budget cash order.

**Driver:** `online` / `offline`, `accept` / `decline`, `at shop`, `bought <amount>`
(plus a photo of the till slip), `delivered`.

## Architecture

- **Backend:** FastAPI + SQLModel + PostgreSQL/PostGIS. Three services do the work:
  `whatsapp` (conversation flows), `dispatch` (nearest-available from the operator's own
  fleet + manual assign), `payment` (prepay-before-dispatch, top-up, refund, cash records).
- **WhatsApp:** Evolution API.
- **Payments:** Paystack (ZAR).
- **Maps:** Google Maps (geocoding + ETAs); PostGIS for distance/geofencing.
- **Dashboard:** one static `frontend/index.html` ops board (live orders, money state,
  one-tap driver assignment). No build step.

```
backend/app/
  core/        config, database
  models/      order, driver, customer, payment
  services/    whatsapp, dispatch, payment
  utils/       maps
  api/v1/      webhooks, orders, drivers, admin
frontend/index.html   operator dashboard
```

## Run it

```bash
cp backend/.env.example backend/.env   # add your Paystack, Google Maps & Evolution keys
docker compose up -d
```

- API + docs: http://localhost:8000/docs
- Dashboard: http://localhost:3000
- Evolution (scan WhatsApp QR, set webhook to `http://backend:8000/webhooks/whatsapp`): http://localhost:8080

Add the operator's drivers via the API:

```bash
curl -X POST "http://localhost:8000/api/v1/drivers/?name=Sipho&phone=27821234567&vehicle=bike"
```

Drivers come online by WhatsApping `online` to the bot number; their phone must match the one
registered above.

## Notes

- Conversation state is in-memory for simplicity — move it to Redis before running more than
  one backend instance.
- Driver location uses WhatsApp live location (via Evolution) or the
  `POST /api/v1/drivers/{id}/location` endpoint if you add a lightweight driver app later.
- A driver carrying both cash and goods is a higher robbery risk than one carrying goods alone —
  worth keeping cash orders to lower-value, known customers, which the threshold helps enforce.
