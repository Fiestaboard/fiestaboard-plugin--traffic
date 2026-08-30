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

The Routes API supports different travel modes:

- **🚗 Drive**: Car/driving directions with live traffic
- **🚴 Bicycle**: Bike routes (bike lanes, paths)
- **🚇 Transit**: Public transportation (bus, train, subway)
- **👣 Walk**: Walking directions

Each mode returns different routes optimized for that transportation type.

## Costs

Google Maps Platform bills the Routes API **per request**, against a free
allowance that is granted **per SKU, per month**. Which SKU a request lands in
depends on what the request asks for. This plugin asks for live traffic
(`routingPreference: TRAFFIC_AWARE_OPTIMAL`), which puts every request in
**Compute Routes Pro**.

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
30-day month. Per configured route:

| Routes | Refresh | Requests/month | Billable (over 5,000) | Cost/month |
|--------|---------|----------------|-----------------------|------------|
| 1 | 5 min | 8,640 | 3,640 | **$36.40** |
| 2 | 5 min | 17,280 | 12,280 | **$122.80** |
| 4 | 5 min | 34,560 | 29,560 | **$295.60** |
| 4 | 15 min | 11,520 | 6,520 | **$65.20** |
| 4 | 40 min | 4,320 | 0 | **free** |
| 1 | 10 min | 4,320 | 0 | **free** |

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

**Route 1: Home to Work (Drive)**
- Origin: `1735 35th Ave, San Francisco, CA 94122`
- Destination: `525 20th St, San Francisco, CA 94107`
- Display Name: `WORK`
- Travel Mode: Drive

**Route 2: Home to Work (Bike)**
- Origin: `1735 35th Ave, San Francisco, CA 94122`
- Destination: `525 20th St, San Francisco, CA 94107`
- Display Name: `WORK-BIKE`
- Travel Mode: Bicycle

**Route 3: Home to Work (Transit)**
- Origin: `1735 35th Ave, San Francisco, CA 94122`
- Destination: `525 20th St, San Francisco, CA 94107`
- Display Name: `WORK-MUNI`
- Travel Mode: Transit

Then in your template:
```
COMMUTE OPTIONS
DRIVE: {{traffic.routes.0.duration_minutes}}m
BIKE: {{traffic.routes.1.duration_minutes}}m
MUNI: {{traffic.routes.2.duration_minutes}}m
```

This lets you compare all three options at a glance! 🚗🚴🚇

