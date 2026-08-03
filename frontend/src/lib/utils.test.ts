import { describe, expect, it } from "vitest";

import { cn } from "./utils";

describe("cn", () => {
  it("merges conditional and conflicting Tailwind classes", () => {
    expect(cn("px-2", false && "hidden", "px-4", { block: true })).toBe("px-4 block");
  });
});
