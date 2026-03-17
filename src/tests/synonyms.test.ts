import { describe, it, expect } from "vitest";
import { getSynonyms, expandWithSynonyms } from "../synonyms.js";

describe("getSynonyms", () => {
    it("returns synonyms for a known term", () => {
        const result = getSynonyms("ticket");
        expect(result).toContain("support case");
        expect(result).toContain("issue");
        expect(result).toContain("request");
    });

    it("is case-insensitive", () => {
        expect(getSynonyms("Ticket")).toEqual(getSynonyms("ticket"));
    });

    it("returns empty array for unknown term", () => {
        expect(getSynonyms("xyzzy123")).toEqual([]);
    });

    it("returns bidirectional synonyms", () => {
        const fromTicket = getSynonyms("ticket");
        expect(fromTicket).toContain("support case");

        const fromSupportCase = getSynonyms("support case");
        expect(fromSupportCase).toContain("ticket");
    });
});

describe("expandWithSynonyms", () => {
    it("returns original text as first element", () => {
        const result = expandWithSynonyms("fix the bug");
        expect(result[0]).toBe("fix the bug");
    });

    it("expands known words with synonyms", () => {
        const result = expandWithSynonyms("ticket");
        expect(result).toContain("ticket");
        expect(result).toContain("support case");
        expect(result).toContain("issue");
    });

    it("expands multiple words independently", () => {
        const result = expandWithSynonyms("open ticket");
        expect(result).toContain("open ticket");
        expect(result).toContain("create");
        expect(result).toContain("submit");
        expect(result).toContain("support case");
        expect(result).toContain("issue");
    });

    it("does not duplicate terms", () => {
        const result = expandWithSynonyms("error bug");
        const unique = new Set(result);
        expect(unique.size).toBe(result.length);
    });

    it("returns only original text for unknown words", () => {
        const result = expandWithSynonyms("xyzzy foobar");
        expect(result).toEqual(["xyzzy foobar"]);
    });
});
