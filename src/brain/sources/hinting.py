"""Which part of a life a link, a sender or an event title belongs to: the hint a source gives each row. Standard
library only, so a script carried to the owner's Mac can use it too."""
from __future__ import annotations

# What a link, a sender or an event title says about which part of a life it belongs to; first matches win in order.
HINTS = (("places", ("maps.", "goo.gl/maps", "yelp.", "tripadvisor.", "airbnb.", "booking.com", "zillow.", "redfin.",
                     "streeteasy.", "expedia.", "flight", "hotel", "trip ", "stay at", " inn", "suites", "hostel", "delta.",
                     "united.", "aa.com", "southwest.", "jetblue.", "alaskaair.", "amtrak.", "hilton.", "marriott.",
                     "hyatt.", "ihg.")),
         ("food & drink", ("opentable.", "resy.", "doordash.", "ubereats.", "seamless.", "tock.", "dinner", "lunch", "brunch",
                           "restaurant", "drinks", "reservation at", "omakase")),
         ("music", ("spotify.", "music.apple.", "soundcloud.", "bandcamp.", "tidal.", "songlink.", "ra.co", "concert", "gig")),
         ("play", ("strava.", "chess.com", "lichess.", "steampowered.", "twitch.tv", "ticketmaster.", "eventbrite.", "partiful.",
                   "gym", "climb", "boulder", "run ", "running", "yoga", "tennis", "game", "match", "hike", "poker")),
         ("health", ("doctor", "dentist", "therapy", "clinic", "physio")),   # said-only: the judging pass never reads it
         ("money", ("venmo.", "paypal.", "coinbase.", "robinhood.", "polymarket.", "kalshi.", "chase.", "americanexpress.",
                    "amex", "capitalone.", "bankofamerica.", "wellsfargo.", "fidelity.", "schwab.", "vanguard.", "wise.com",
                    "cash.app", "mint.", "irs.gov", "sofi.", "chime.", "betterment.",
                    "wealthfront.", "etrade.")),   # said-only too
         ("work", ("meeting", "standup", "stand-up", "sync", "1:1", "interview", "retro", "sprint", "demo day", "all hands",
                   "github.", "gitlab.", "linear.app", "sentry", "notion.", "atlassian.", "slack.", "figma.",
                   "vercel.", "gusto.", "jira", "zoom.us", "calendly.", "upwork.")),
         ("reading & ideas", ("book club", "lecture", "talk:", "reading")))
CONSUMER = {"gmail.com", "googlemail.com", "icloud.com", "me.com", "mac.com", "outlook.com", "hotmail.com", "live.com",
            "yahoo.com", "aol.com", "proton.me", "protonmail.com", "fastmail.com", "hey.com"}


def _matches(mark: str, text: str) -> bool:
    """A word or phrase matches anywhere; a domain mark (`aa.com`, `resy.`) only where a domain starts, so bigdataa.com
    is not an airline."""
    if "." not in mark or mark.startswith(" "):
        return mark in text
    at = text.find(mark)
    while at >= 0:
        if at == 0 or text[at - 1] in "./@ :":
            return True
        at = text.find(mark, at + 1)
    return False


def hints(text: str, default: str = "reading & ideas") -> tuple[str, ...]:
    text = text.casefold()
    return tuple(facet for facet, marks in HINTS if any(_matches(mark, text) for mark in marks)) or (default,)


def work_account(address: str) -> bool:
    """An account on a domain that is not a consumer mail service is one the owner holds through their work."""
    return "@" in address and address.rsplit("@", 1)[1].casefold() not in CONSUMER


def domain(host: str) -> str:
    """The domain a sender belongs to, without the subdomains its mail goes out from (email.venmo.com is venmo.com)."""
    labels = host.casefold().strip(".").split(".")
    keep = 3 if len(labels) >= 3 and labels[-2] in ("co", "com", "org", "net", "ac", "gov") and len(labels[-1]) == 2 else 2
    return ".".join(labels[-keep:])
