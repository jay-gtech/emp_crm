import pytest
from playwright.sync_api import Page, expect

@pytest.fixture
def run_employee(page: Page, live_server, ui_seed_db):
    page.goto(f"{live_server}/auth/login")
    page.fill('input[name="email"]', "employee@test.com")
    page.fill('input[name="password"]', "testpass123")
    page.click('button[type="submit"]')
    page.wait_for_url(f"{live_server}/dashboard/")
    return page

def test_employee_apply_leave(run_employee: Page, live_server):
    run_employee.goto(f"{live_server}/leaves/")
    
    # Find form and apply
    run_employee.fill('input[name="start_date"]', "2026-10-01")
    run_employee.fill('input[name="end_date"]', "2026-10-05")
    try:
        run_employee.fill('textarea[name="reason"]', "Playwright vacation request")
        run_employee.select_option('select[name="leave_type"]', "casual")
    except:
        pass
        
    run_employee.click('button[type="submit"]')
    expect(run_employee).to_have_url(f"{live_server}/leaves/")
    
    # Verify table shows pending status or request
    expect(run_employee.locator("body")).to_contain_text("Playwright vacation request")
