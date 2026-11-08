"""Which part of a life a link, a sender or an event title belongs to: the hint a source gives each row. Standard
library only, so a script carried to the owner's Mac can use it too."""
from __future__ import annotations

# What a link, a sender or an event title says about which part of a life it belongs to; first matches win in order.
HINTS = (("places", ("maps.", "goo.gl/maps", "yelp.", "tripadvisor.", "airbnb.", "booking.com", "zillow.", "redfin.",
                     "streeteasy.", "expedia.", "flight", "hotel", "trip ")),
         ("food & drink", ("opentable.", "resy.", "doordash.", "ubereats.", "seamless.", "tock.", "dinner", "lunch", "brunch",
                           "restaurant", "drinks")),
         ("music", ("spotify.", "music.apple.", "soundcloud.", "bandcamp.", "tidal.", "songlink.", "ra.co", "concert", "gig")),
         ("play", ("strava.", "chess.com", "lichess.", "steampowered.", "twitch.tv", "ticketmaster.", "eventbrite.", "partiful.",
                   "gym", "climb", "boulder", "run ", "running", "yoga", "tennis", "game", "match", "hike", "poker")),
         ("health", ("doctor", "dentist", "therapy", "clinic", "physio")),   # said-only: the judging pass never reads it
         ("reading & ideas", ("book club", "lecture", "talk:", "reading")))


def hints(text: str, default: str = "reading & ideas") -> tuple[str, ...]:
    text = text.casefold()
    return tuple(facet for facet, marks in HINTS if any(mark in text for mark in marks)) or (default,)
