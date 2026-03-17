// ---------------------------------------------------------------------------
// Synonym expansion for fuzzy session search
// ---------------------------------------------------------------------------

// Each group maps specific/uncommon terms to their synonyms.
// IMPORTANT: Avoid overly common words that appear in most coding sessions
// (e.g. "issue", "error", "request", "fix", "bug"). These cause massive
// false positive rates when used in broad searches. Only include terms
// that are specific enough to be meaningful search discriminators.
const SYNONYM_GROUPS: string[][] = [
    ["ticket", "support case", "support ticket", "bug report"],
    ["deploy", "release", "ship", "push to production"],
    ["crash", "exception", "stack trace", "segfault"],
    ["config", "configuration", "settings", "preferences"],
    ["auth", "authentication", "login", "sign in", "sso"],
    ["db", "database", "rds", "postgres", "sql"],
    ["webhook", "callback"],
    ["sync", "synchronize", "synchronization"],
    ["missing", "not found", "absent", "disappeared"],
    ["duplicate", "double", "repeated", "twice"],
    ["k8s", "kubernetes"],
    ["ci/cd", "pipeline", "github actions"],
    ["pr", "pull request", "merge request"],
    ["repo", "repository"],
    ["infra", "infrastructure"],
    ["cred", "credential", "secret", "password", "token"],
    ["migration", "migrate", "schema change"],
    ["rollback", "revert", "undo"],
    ["outage", "downtime", "incident"],
    ["latency", "slow", "performance", "timeout"],
    ["retry", "backoff", "rate limit", "throttle"],
];

const synonymIndex = new Map<string, string[]>();

for (const group of SYNONYM_GROUPS) {
    for (const term of group) {
        const others = group.filter((t) => t !== term);
        const existing = synonymIndex.get(term.toLowerCase());
        if (existing) {
            for (const o of others) {
                if (!existing.includes(o.toLowerCase())) {
                    existing.push(o.toLowerCase());
                }
            }
        } else {
            synonymIndex.set(
                term.toLowerCase(),
                others.map((t) => t.toLowerCase())
            );
        }
    }
}

/** Get synonyms for a single term. Returns empty array if no synonyms found. */
export function getSynonyms(term: string): string[] {
    return synonymIndex.get(term.toLowerCase()) ?? [];
}

/**
 * Expand a search text with synonym alternatives.
 * Returns an array where the first element is the original search text,
 * followed by synonym terms found for any word in the input.
 */
export function expandWithSynonyms(searchText: string): string[] {
    const results = [searchText];
    const words = searchText.toLowerCase().split(/\s+/).filter(Boolean);

    for (const word of words) {
        const syns = synonymIndex.get(word);
        if (syns) {
            for (const syn of syns) {
                if (!results.includes(syn)) {
                    results.push(syn);
                }
            }
        }
    }

    return results;
}
