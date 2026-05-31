"""Rule-based extraction of structured business opportunities from Reddit posts."""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

# Maps keyword patterns → industry label
_INDUSTRY_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"warehouse|inventory|supply.?chain|logistics|freight|shipping|3pl", re.I), "Logistics"),
    (re.compile(r"hospital|clinic|patient|ehr|medical|healthcare|pharmacy|nurse|doctor", re.I), "Healthcare"),
    (re.compile(r"retail|e.?commerce|store|shopify|amazon.?seller|product.?listing", re.I), "Retail"),
    (re.compile(r"restaurant|kitchen|pos|food.?(service|delivery)|menu", re.I), "Food & Beverage"),
    (re.compile(r"construc|contractor|site.?manager|blueprint|project.?bid", re.I), "Construction"),
    (re.compile(r"manufactur|factory|assembly|production.?line|cnc|machining", re.I), "Manufacturing"),
    (re.compile(r"hr|human.?resource|hiring|recruit|onboard|payroll|employee", re.I), "Human Resources"),
    (re.compile(r"account|invoice|bookkeep|tax|payable|receivable|quickbooks", re.I), "Finance & Accounting"),
    (re.compile(r"real.?estate|property|landlord|tenant|lease|mortgage", re.I), "Real Estate"),
    (re.compile(r"software|saas|engineer|developer|devops|backend|frontend|api", re.I), "Software / Tech"),
    (re.compile(r"market|seo|campaign|lead.?gen|ads|email.?blast|funnel", re.I), "Marketing"),
    (re.compile(r"legal|law|compliance|contract|litigation|attorney", re.I), "Legal"),
    (re.compile(r"school|educat|student|teacher|lms|course|curriculum", re.I), "Education"),
    (re.compile(r"farm|agri|crop|harvest|livestock|irrigation", re.I), "Agriculture"),
]

# Maps keyword patterns → buyer type
_BUYER_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"warehouse|inventory|stock|shelf|pallet|forklift", re.I), "Warehouse Manager"),
    (re.compile(r"hospital|clinic|patient|nurse|doctor|ehr|medical", re.I), "Healthcare Administrator"),
    (re.compile(r"hr|human.?resource|hiring|recruit|onboard|payroll", re.I), "HR Manager"),
    (re.compile(r"account|invoice|bookkeep|tax|payable|receivable", re.I), "Finance Manager"),
    (re.compile(r"real.?estate|property|landlord|tenant|lease", re.I), "Property Manager"),
    (re.compile(r"software|developer|engineer|devops|backend|frontend", re.I), "Engineering Manager"),
    (re.compile(r"restaurant|kitchen|pos|food|menu", re.I), "Restaurant Owner"),
    (re.compile(r"construc|contractor|site|blueprint|bid", re.I), "Project Manager"),
    (re.compile(r"manufactur|factory|assembly|production", re.I), "Operations Manager"),
    (re.compile(r"retail|e.?commerce|store|shopify|amazon", re.I), "E-commerce Owner"),
    (re.compile(r"market|seo|campaign|ads|funnel|lead", re.I), "Marketing Manager"),
    (re.compile(r"legal|law|compliance|contract|attorney", re.I), "Legal Counsel"),
    (re.compile(r"school|educat|student|teacher|lms|course", re.I), "Educational Administrator"),
    (re.compile(r"ceo|founder|startup|owner|co.?founder", re.I), "Business Owner / Founder"),
]

# Words that signal high severity (pain is deep / expensive)
_HIGH_SEVERITY = re.compile(
    r"nightmare|horrible|terrible|broken|disaster|chaos|massive.?waste|"
    r"cost.?us|losing.?(money|revenue|clients|customers)|critical|"
    r"frustrated|fed.?up|unbearable|impossible|hopeless",
    re.I,
)
_MED_SEVERITY = re.compile(
    r"painful|annoying|slow|inefficient|manual|tedious|error.?prone|"
    r"inconsistent|unreliable|clunky|outdated|painful|struggle",
    re.I,
)

# Words that signal high urgency (needs a fix now)
_HIGH_URGENCY = re.compile(
    r"urgent|asap|immediately|right.?now|need.?now|today|can't.?wait|"
    r"deadline|running.?out|losing|crisis|emergency|critical",
    re.I,
)
_MED_URGENCY = re.compile(
    r"soon|planning|looking.?for|need.?to|want.?to|trying.?to|"
    r"wish|would.?love|searching.?for|considering",
    re.I,
)

# Phrases that describe a problem — used to build a condensed problem label
_PROBLEM_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"manual|still.?using.?excel|spreadsheet|paper", re.I), "Manual tracking"),
    (re.compile(r"can.?t.?track|hard.?to.?track|no.?visibility", re.I), "Lack of tracking / visibility"),
    (re.compile(r"(takes?|waste[sd]?).{0,20}(hour|day|week|time)", re.I), "Time-consuming process"),
    (re.compile(r"error|mistake|inaccura|wrong.?data|data.?quality", re.I), "Data errors / inaccuracy"),
    (re.compile(r"slow|bottleneck|delay|backlog", re.I), "Slow / bottlenecked workflow"),
    (re.compile(r"expensive|cost.?too.?much|overpriced|budget", re.I), "High cost / budget pressure"),
    (re.compile(r"integrat|sync|connect.{0,15}system|silos?", re.I), "Lack of system integration"),
    (re.compile(r"communicat|coordinat|miscommun", re.I), "Poor communication / coordination"),
    (re.compile(r"compli|regulat|audit|legal|gdpr", re.I), "Compliance / regulatory burden"),
    (re.compile(r"scal|grow|can.?t.?keep.?up|overwhelm", re.I), "Scaling challenges"),
    (re.compile(r"report|dashboard|insight|analytic|metric", re.I), "Lack of reporting / insights"),
    (re.compile(r"onboard|training|ramp.?up|hard.?to.?learn", re.I), "Onboarding / training difficulty"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_match(text: str, rules: list[tuple[re.Pattern, str]], default: str) -> str:
    for pattern, label in rules:
        if pattern.search(text):
            return label
    return default


def _severity_score(text: str) -> int:
    if _HIGH_SEVERITY.search(text):
        return 9
    if _MED_SEVERITY.search(text):
        return 6
    return 3


def _urgency_score(text: str) -> int:
    if _HIGH_URGENCY.search(text):
        return 9
    if _MED_URGENCY.search(text):
        return 6
    return 3


def _extract_problem_label(text: str, fallback_title: str) -> str:
    for pattern, label in _PROBLEM_PATTERNS:
        if pattern.search(text):
            return label
    # Fall back to a truncated title (first 60 chars, stripped at a word boundary)
    truncated = fallback_title[:60].rsplit(" ", 1)[0]
    return truncated if truncated else fallback_title


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_problem(post: dict) -> dict:
    """Extract a structured business opportunity from a Reddit post dict.

    Input keys expected: title, body (others are ignored).
    Returns: problem, buyer_type, industry, severity_score, urgency_score.
    """
    title: str = post.get("title", "")
    body: str = post.get("body", "")
    combined = f"{title} {body}"

    problem = _extract_problem_label(combined, title)
    buyer_type = _first_match(combined, _BUYER_RULES, "Business Owner")
    industry = _first_match(combined, _INDUSTRY_RULES, "General Business")
    severity = _severity_score(combined)
    urgency = _urgency_score(combined)

    return {
        "problem": problem,
        "buyer_type": buyer_type,
        "industry": industry,
        "severity_score": severity,
        "urgency_score": urgency,
    }
