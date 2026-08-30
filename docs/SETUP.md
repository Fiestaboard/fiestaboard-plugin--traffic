# Traffic Feature Setup Guide

## Google Routes API Setup

The Traffic feature uses Google's Routes API (v2) to get real-time travel times. Here's how to set it up properly.

### Step 1: Enable the Routes API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Select your project (or create a new one)
3. Navigate to **APIs & Services** → **Library**
4. Search for "**Routes API**" (NOT "Directions API" - that's the old one)
5. Click on "**Routes API**" and click **Enable**

### Step 2: Set Up Billing

⚠️ **Important**: The Routes API requires a billing account, and this plugin can cost real money. Read this section before you turn the plugin on.

1. Go to **Billing** in the Google Cloud Console
2. Link a billing account to your project
3. Set a **budget alert** on the project (Billing → Budgets & alerts) so a mistake cannot run away from you

> **The $200/month credit is gone.** Google retired the flat, platform-wide $200 monthly credit on **1 March 2025** and replaced it with a smaller free allowance *per SKU*. Older guides — including previous versions of this one — that tell you a commute board is free are wrong.

See [Costs](#costs) below for what this plugin actually bills.

### Step 3: Create an API Key

1. Go to **APIs & Services** → **Credentials**
2. Click **Create Credentials** → **API Key**
3. Copy the API key
4. **Recommended**: Click "Restrict Key" to secure it:
   - Under "API restrictions", select "Restrict key"
   - Choose "Routes API" from the list
   - Under "Application restrictions", you can:
     - Leave unrestricted for Docker/local use
     - Or restrict by IP if you know your server's IP

### Step 4: Add to Your Configuration

Add the API key to your configuration:

**Option A: Via Web UI (Recommended)**
1. Open http://localhost:4420
2. Go to the **Integrations** page
3. Find **Traffic** and toggle the plugin on
4. Paste your API key in the "Google Routes API Key" field
5. Click Save

**Option B: Via Environment Variable**
Add to your `.env` file:
```bash
GOOGLE_ROUTES_API_KEY=your_api_key_here
```

### Step 5: Add a Route

1. In the web UI, go to the **Traffic** plugin on the **Integrations** page
2. Click **Add Routes**
3. Enter:
   - **Origin**: Your home address or `40.7128,-74.0060` (coordinates work too)
   - **Destination**: Your work address or `40.7580,-73.9855`
   - **Display Name**: `WORK`
   - **Travel Mode**: Drive, Bicycle, Transit, Walk, or Motorcycle / Scooter
     (see [Travel Modes](#travel-modes) — it changes what the delay numbers
     mean, and what you are billed)
4. Click **Save**

You can monitor up to **4 routes**. Each one is a separate billable API
request on every refresh — see [Costs](#costs).

There is no per-route validation button. To confirm the route works, put
`{{traffic.formatted}}` on a page and look at the board — or check the logs
(see [Troubleshooting](#troubleshooting)). A route Google cannot resolve shows
up as an error in the logs rather than as an inline warning in the form.

## Troubleshooting

### Error: "403 Forbidden"

**Cause**: Routes API is not enabled or billing is not set up.

**Fix**:
1. Make sure you enabled "Routes API" (not "Directions API")
2. Verify billing is set up in Google Cloud Console
3. Wait 1-2 minutes after enabling the API

### Error: "400 Bad Request"

**Cause**: The address format is invalid or Google can't geocode it.

**Fix**:
1. Try using the full address with city and state: `123 Main St, San Francisco, CA 94102`
2. Or use coordinates: `40.7128,-74.0060`
3. Avoid ambiguous addresses like "Main Street"

### The Board Shows `???` Instead of a Time

`???` is what the template engine renders when a value is missing.

**Possible causes**:
1. API key is incorrect, or its restrictions are too strict
2. Network connectivity issues
3. Google returned no route for that origin/destination pair

**Fix**:
1. Double-check your API key in Settings
2. Check Docker logs: `docker-compose -f docker-compose.dev.yml logs fiestaboard | grep traffic`
3. Try a different address format

### A Transit Route Shows `NO DATA` or `???`

**Cause**: Google returned no route. For Transit this is usually correct rather
than broken — there is no service on that leg at the time the board refreshed.
Late at night, or on a route with no transit coverage, an empty answer is the
honest one.

**Fix**: nothing to fix if the time of day explains it. If a transit route is
empty at rush hour, check that both endpoints are near actual stops and try
coordinates instead of addresses.

The route keeps its slot in the `routes` array either way, so your other routes
do not move.

### Using Coordinates Instead of Addresses

If addresses aren't working, you can use latitude,longitude coordinates:

1. Go to [Google Maps](https://maps.google.com)
2. Right-click on your location
3. Click the coordinates at the top (e.g., "40.7128, -74.0060")
4. Use this format in the Traffic settings: `40.7128,-74.0060` (no spaces)

**Example**:
- Origin: `40.7128,-74.0060` (New York City)
- Destination: `40.7580,-73.9855` (Central Park)

## Address Format Tips

### ✅ Good Address Formats

- `123 Main St, New York, NY 10001`
- `456 Park Ave, New York, NY 10022`
- `San Francisco International Airport, CA`
- `40.7128,-74.0060` (coordinates)

### ❌ Bad Address Formats

- `Main Street` (too vague)
- `Downtown` (ambiguous)
- `123` (incomplete)
- `40.7128, -74.0060` (space after comma - remove it)

## Travel Modes

Each route has its own **Travel Mode**, set per route in the settings form. It
defaults to **Drive**, so routes you saved before this setting existed keep
behaving exactly as they did.

| Mode | Stored as | Live traffic delays? | Billed as |
|------|-----------|----------------------|-----------|
| 🚗 Drive | `DRIVE` | **yes** | Compute Routes Pro |
| 🏍 Motorcycle / Scooter | `TWO_WHEELER` | **yes** | Compute Routes **Enterprise** |
| 🚴 Bicycle | `BICYCLE` | no | Compute Routes Essentials |
| 🚇 Transit | `TRANSIT` | no | Compute Routes Essentials |
| 👣 Walk | `WALK` | no | Compute Routes Essentials |

The mode a route used is available in templates as
`{{traffic.routes.0.travel_mode}}`.

### ⚠️ Only Drive and Motorcycle have traffic numbers

This is the part worth reading twice, because the board does not make it
obvious.

Google will only accept a live-traffic request (`routingPreference`) for
`DRIVE` and `TWO_WHEELER`. Sending it for a walk, bike or transit route is not
ignored — it is a **400 Bad Request**, and the route would show nothing at all.
So the plugin omits it for those modes.

The consequence: for Bicycle, Transit and Walk, Google returns a trip duration
with no traffic model behind it, which means

- `delay_minutes` is always **0**
- `traffic_status` is always **LIGHT**
- `traffic_color` is always **green**

Those are not measurements. They are what "there is no traffic model for
walking" looks like once it reaches the board. The duration itself is real and
useful; the delay and status are not.

**We have deliberately left it that way** rather than inventing a substitute
signal — a bike route is not "light traffic", and a made-up number on a
kitchen wall is worse than an obviously flat one. If you display several modes
side by side, show `duration_minutes` and leave the status tile off non-drive
routes:

```
{{traffic.routes.0.traffic_color}} DRIVE {{traffic.routes.0.duration_minutes}}m
  BIKE  {{traffic.routes.1.duration_minutes}}m
  MUNI  {{traffic.routes.2.duration_minutes}}m
```

### When a route has no answer

Transit is the common case: ask for a bus at 3am and Google correctly returns
no route at all. A route that comes back empty — for that reason, or because
the request failed — **keeps its position** in the `routes` array, so
`{{traffic.routes.2.duration_minutes}}` never starts quietly showing you a
different commute. It reports:

- `duration_minutes` and `delay_minutes` as no value, which the template engine
  renders as `???`
- `traffic_status` as `UNKNOWN` and `traffic_color` as empty (no colour tile)
- `formatted` as `MUNI: NO DATA`
- `available` as false, so you can branch on it

`route_count` counts the routes you configured, not the ones that answered.
The plugin only reports itself as unavailable when *every* route came back
empty.

## Costs

Google Maps Platform bills the Routes API **per request**, against a free
allowance that is granted **per SKU, per month**. Which SKU a request lands in
depends on what the request asks for, which for this plugin means: **the
travel mode you pick decides the price**.

- **Drive** asks for live traffic (`routingPreference: TRAFFIC_AWARE_OPTIMAL`),
  which is a **Compute Routes Pro** request.
- **Bicycle, Transit and Walk** cannot ask for live traffic at all, so they are
  plain **Compute Routes Essentials** requests — cheaper, and with double the
  free allowance.
- **Motorcycle / Scooter** (`TWO_WHEELER`) is listed by Google under
  **Compute Routes Enterprise**: the most expensive SKU, with a fifth of
  Drive's free allowance. Pick it because you ride, not to experiment.

| SKU | Free calls/month | Price per 1,000 after that |
|-----|------------------|----------------------------|
| Compute Routes Essentials | 10,000 | $5.00 |
| **Compute Routes Pro** — what this plugin uses | **5,000** | **$10.00** |
| Compute Routes Enterprise | 1,000 | $15.00 |

*(First volume tier, i.e. up to 100,000 calls/month. Verified against
[Google's core services pricing list](https://developers.google.com/maps/billing-and-pricing/pricing)
and the [Routes API usage and billing guide](https://developers.google.com/maps/documentation/routes/usage-and-billing).
Prices change — re-check before you rely on them.)*

### What that means for a commute board

One route refreshed every 5 minutes is `43,200 / 5 = 8,640` requests per
30-day month. Per configured **Drive** route:

| Routes | Refresh | Requests/month | Billable (over 5,000) | Cost/month |
|--------|---------|----------------|-----------------------|------------|
| 1 | 5 min | 8,640 | 3,640 | **$36.40** |
| 2 | 5 min | 17,280 | 12,280 | **$122.80** |
| 4 | 5 min | 34,560 | 29,560 | **$295.60** |
| 4 | 15 min | 11,520 | 6,520 | **$65.20** |
| 4 | 40 min | 4,320 | 0 | **free** |
| 1 | 10 min | 4,320 | 0 | **free** |

The allowances are **per SKU**, so modes draw from separate buckets. The same
one-route-every-5-minutes board costs:

| Mode | SKU | Free | Billable | Cost/month |
|------|-----|------|----------|------------|
| Bicycle / Transit / Walk | Essentials | 10,000 | 0 | **free** |
| Drive | Pro | 5,000 | 3,640 | **$36.40** |
| Motorcycle / Scooter | Enterprise | 1,000 | 7,640 | **$114.60** |

A board showing *drive time and bike time* to the same place is not twice the
price of the drive route — the bike route is free until you pass 10,000
Essentials calls.

The plugin's default refresh is 5 minutes and its floor is 60 seconds. At the
60-second floor a single route is 43,200 requests/month — **$382/month**.

### Staying inside the free tier

The free allowance is 5,000 Compute Routes Pro calls per month across your
whole Google Cloud project. To stay under it:

```
refresh_seconds >= (number of routes) x 2,592,000 / 5,000
                =  (number of routes) x 519
```

- 1 route → refresh every **10 minutes** (4,320 calls/month)
- 2 routes → refresh every **20 minutes**
- 4 routes → refresh every **40 minutes**

Only Drive routes count against that 5,000. Walk, bike and transit routes have
their own 10,000-call Essentials allowance, and a `TWO_WHEELER` route has its
own 1,000-call Enterprise one.

A commute board only needs to be right when you are looking at it. A
15–20 minute refresh is usually indistinguishable on the board and is an order
of magnitude cheaper.

### Rate limits

No hard request-rate limit beyond the per-minute quotas in your Cloud project;
the constraint that matters is cost, not throttling.

## Privacy & Security

- Your API key is stored securely in the Docker container
- Routes API requests go directly from your server to Google
- No route data is stored permanently
- Consider using API key restrictions in production

## Need Help?

1. Check the [Google Routes API documentation](https://developers.google.com/maps/documentation/routes)
2. View your API usage in [Google Cloud Console](https://console.cloud.google.com/)
3. Check Docker logs for detailed error messages
4. Make sure you're using Routes API (v2), not the older Directions API

## Example Configuration

![Traffic Display](./board-display.png)

Here's a complete example for a morning commute:

Three ways to make the same trip, so you can pick one on the way out the door.

**Route 1: Home to Work (Drive)**
- Origin: `1735 35th Ave, San Francisco, CA 94122`
- Destination: `525 20th St, San Francisco, CA 94107`
- Display Name: `WORK`
- Travel Mode: Drive

**Route 2: Home to Work (Bike)**
- Origin: `1735 35th Ave, San Francisco, CA 94122`
- Destination: `525 20th St, San Francisco, CA 94107`
- Display Name: `BIKE`
- Travel Mode: Bicycle

**Route 3: Home to Work (Transit)**
- Origin: `1735 35th Ave, San Francisco, CA 94122`
- Destination: `525 20th St, San Francisco, CA 94107`
- Display Name: `MUNI`
- Travel Mode: Transit

Then in your template:
```
COMMUTE OPTIONS
{{traffic.routes.0.traffic_color}} DRIVE {{traffic.routes.0.duration_minutes}}m
  BIKE  {{traffic.routes.1.duration_minutes}}m
  MUNI  {{traffic.routes.2.duration_minutes}}m
```

The colour tile is on the drive route only, because that is the only one of the
three with a real traffic reading behind it — see
[Travel Modes](#travel-modes). If the 3am bus does not run, the MUNI line shows
`???` and the other two are unaffected.

**What this costs**: one Pro request (drive) and two Essentials requests (bike,
transit) per refresh. At the default 5-minute refresh that is 8,640 Pro calls
(3,640 over the allowance → $36.40) plus 17,280 Essentials calls (7,280 over →
$36.40): about **$73/month**.

Set the refresh to **15 minutes** and the same board is **free** — 2,880 Pro
calls and 5,760 Essentials calls, both inside their allowances. See
[Costs](#costs).

