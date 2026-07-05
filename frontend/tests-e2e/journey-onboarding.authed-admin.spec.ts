// Journey 1 — New user onboarding full chain (P0).
//
// Validates the complete cross-component flow:
//   admin creates a user  →  logs out  →  new user logs in  →
//   dashboard renders with correct role-scoped UI.
//
// Also serves as the first validation that the chromium-authed-user project
// can be used for non-admin specs (the new user is a regular 'user' role).
// Runs under chromium-authed-admin for the admin portion; the new user login
// step exercises the login page UI directly.

import { test, expect } from '@playwright/test';
import {
  deleteUser,
  loginAndGetContext,
} from './helpers/api';

test.describe('Journey 1 — new user onboarding chain', () => {
  // Unique username per run to avoid collisions across parallel/serial runs.
  const newUserPrefix = `e2e_j1_${Date.now()}`;
  let newUsername = '';
  const newPassword = 'J1-Pass-2026!Strong';

  test.afterAll(async () => {
    if (!newUsername) return;
    const adminCtx = await loginAndGetContext('admin');
    try {
      await deleteUser(adminCtx, newUsername);
    } finally {
      await adminCtx.dispose();
    }
  });

  test('J1.1 admin creates a new user, then that user can log in and sees user-scoped UI', async ({
    page,
  }) => {
    // ── Step 1: Admin is logged in (storageState), verify dashboard + admin gate ──
    await page.goto('/');
    await expect(page.getByTestId('profile-username')).toHaveText('admin');
    await expect(page.getByTestId('open-users-button')).toBeVisible();

    // ── Step 2: Open user management modal and create a new user via UI ──
    await page.getByTestId('open-users-button').click();
    // Semi Modal: use toBeAttached (outer container may be CSS-hidden).
    await expect(page.getByTestId('user-modal')).toBeAttached({ timeout: 10000 });
    await expect(page.getByTestId('user-new-username')).toBeVisible({ timeout: 5000 });

    newUsername = `${newUserPrefix}_${Math.random().toString(36).slice(2, 6)}`;

    // Fill the new-user form fields.
    // Role select (Semi Select, no testid) defaults to 'user' — no assertion needed.
    await page.getByTestId('user-new-username').fill(newUsername);
    await page.getByTestId('user-new-password').fill(newPassword);

    // Submit and verify the user appears in the table.
    await page.getByTestId('user-add-submit').click();
    await expect(
      page.locator('[data-testid^="user-row-"]').filter({ hasText: newUsername }),
    ).toBeVisible({ timeout: 10000 });

    // Close modal via Escape (Semi Modal close icon is outside the clickable viewport).
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('user-modal')).toBeHidden({ timeout: 5000 });

    // ── Step 3: Admin logs out via UI ──
    await page.getByTestId('logout-button').click();

    // Should land on login screen.
    await expect(page.getByTestId('login-title')).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId('stat-total')).toBeHidden();
    await expect(page.getByTestId('open-trigger-button')).toBeHidden();

    // ── Step 4: New user logs in via UI ──
    await page.getByTestId('login-username').fill(newUsername);
    await page.getByTestId('login-password').fill(newPassword);
    await page.getByTestId('login-submit').click();

    // Dashboard renders for the new user.
    await expect(page.getByTestId('profile-username')).toHaveText(newUsername, {
      timeout: 10000,
    });
    await expect(page.getByTestId('profile-role')).toHaveText('user');

    // ── Step 5: Verify role-scoped UI — non-admin CANNOT see admin-only elements ──
    await expect(page.getByTestId('open-users-button')).toBeHidden({ timeout: 5000 });
    await expect(page.getByTestId('open-trigger-button')).toBeVisible();
    await expect(page.getByTestId('stat-total')).toBeVisible();

    // ── Step 6: Verify runs area is accessible (owner-scoped data isolation) ──
    await expect(page.getByTestId('execution-records-title')).toBeVisible();
  });
});
