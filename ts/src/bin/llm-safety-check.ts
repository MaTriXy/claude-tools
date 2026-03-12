#!/usr/bin/env node
// ---------------------------------------------------------------------------
// CLI entry point for the LLM safety check hook
//
// Usage in ~/.claude/settings.json:
//   "hooks": {
//     "PreToolUse": [{
//       "matcher": "Bash",
//       "hooks": ["node /path/to/claude-tools/ts/dist/bin/llm-safety-check.js"]
//     }]
//   }
// ---------------------------------------------------------------------------

import { processHookInput } from "../llm-safety-check.js";
import type { HookInput } from "../types.js";

async function main(): Promise<void> {
    let raw = "";
    for await (const chunk of process.stdin) {
        raw += chunk;
    }

    let input: HookInput;
    try {
        input = JSON.parse(raw);
    } catch {
        process.stderr.write("LLM safety check: failed to parse stdin as JSON\n");
        process.exit(0);
    }

    const result = await processHookInput(input);

    if (!result) {
        // No decision or API failure - fall through to normal handling
        process.exit(0);
    }

    if (result.decision === "allow") {
        const output = {
            hookSpecificOutput: {
                hookEventName: "PreToolUse",
                permissionDecision: "allow",
                permissionDecisionReason: result.reason,
            },
        };
        process.stdout.write(JSON.stringify(output) + "\n");
        process.exit(0);
    } else if (result.decision === "deny") {
        process.stderr.write(`Blocked by safety check: ${result.reason}\n`);
        process.exit(2);
    }

    // Anything else - fall through
    process.exit(0);
}

main().catch((err) => {
    process.stderr.write(`LLM safety check error: ${err}\n`);
    process.exit(0);
});
