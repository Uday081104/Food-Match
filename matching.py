"""
matching.py — core matching logic for the Surplus Food Matcher.

Two matching modes are supported:
1. Pincode match (simplest, works with zero geocoding — good for MVP/pilot)
2. Radius match using haversine distance, if lat/long are available

For a hyperlocal pilot (one neighborhood), pincode matching alone is
usually good enough. Radius matching is included so you can grow beyond
a single pincode later without rewriting anything.
"""

import math
from db import get_db


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between two points, in kilometers."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def find_matching_ngos(donor_pincode, donor_lat=None, donor_lon=None, radius_km=5):
    """
    Returns a list of NGO rows (as dicts) eligible to receive a broadcast
    for a new offer, ordered by proximity (closest first when lat/lon exist).
    Only NGOs marked is_available=1 are matched.
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM ngos WHERE is_available = 1"
        ).fetchall()

    ngos = [dict(r) for r in rows]

    # Prefer exact pincode match first (cheapest, most reliable for hyperlocal pilots)
    same_pincode = [n for n in ngos if n["pincode"] == donor_pincode]

    if same_pincode:
        return same_pincode

    # Fall back to radius match if coordinates are available
    if donor_lat is not None and donor_lon is not None:
        in_radius = []
        for n in ngos:
            if n["latitude"] is not None and n["longitude"] is not None:
                dist = haversine_km(donor_lat, donor_lon, n["latitude"], n["longitude"])
                if dist <= radius_km:
                    n["_distance_km"] = round(dist, 2)
                    in_radius.append(n)
        in_radius.sort(key=lambda n: n["_distance_km"])
        return in_radius

    return []  # no match found


def claim_offer(offer_id, ngo_id):
    """
    Attempts to claim an offer. Returns (success: bool, message: str).
    Uses an atomic UPDATE ... WHERE status='open' to prevent two NGOs
    both winning the same offer in a race condition.
    """
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE offers SET status = 'claimed' WHERE id = ? AND status = 'open'",
            (offer_id,),
        )
        if cur.rowcount == 0:
            return False, "This offer has already been claimed by another NGO."

        conn.execute(
            "INSERT INTO claims (offer_id, ngo_id) VALUES (?, ?)",
            (offer_id, ngo_id),
        )
        return True, "Offer successfully claimed."
