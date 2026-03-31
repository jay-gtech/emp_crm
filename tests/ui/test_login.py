import pytest
from playwright.sync_api import Page, expect

def test_login_success(page: Page, live_server, ui_seed_db):
    page.goto(f"{live_server}/auth/login")
    page.fill('input[name="email"]', "admin@test.com")
    page.fill('input[name="password"]', "testpass123")
    page.click('button[type="submit"]')
    
    # Wait for navigation and verify the dashboard loaded
    expect(page).to_have_url(f"{live_server}/dashboard/")
    # Find something generic to ensure dashboard rendered
    expect(page.locator("body")).to_contain_text("Dashboard")

def test_login_failure(page: Page, live_server, ui_seed_db):
    page.goto(f"{live_server}/auth/login")
    page.fill('input[name="email"]', "fake@test.com")
    page.fill('input[name="password"]', "wrongpass")
    page.click('button[type="submit"]')
    
    # Expect error message
    expect(page.locator(".alert, .text-danger")).to_contain_text("Invalid email or password", ignore_case=True)
