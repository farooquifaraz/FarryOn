import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

// Every test starts with an empty tab: no tokens, no rendered tree, no
// leftover fetch stub. Without this, a test that signs someone in hands the
// next one a session it never asked for — and the admin gate tests are
// precisely the ones that must never see a stale token.
beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
