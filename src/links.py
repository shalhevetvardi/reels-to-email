"""The one definition of an Instagram link, shared by the bot (which finds it in a
message) and the GitHub fallback job (which accepts only a whole link)."""
import re

# Match Instagram links: reels, posts, IGTV, share links
INSTAGRAM_URL_PATTERN = re.compile(
    r"(https?://)?(www\.)?(instagram\.com|instagr\.am)/(p|reel|reels|tv|share)/[\w\-/?=&]+",
    re.IGNORECASE,
)
