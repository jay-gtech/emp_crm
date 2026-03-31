import pytest
import re
from playwright.sync_api import Page, expect

@pytest.fixture
def logged_in_admin(page: Page, live_server, ui_seed_db):
    page.goto(f"{live_server}/auth/login")
    page.fill('input[name="email"]', "admin@test.com")
    page.fill('input[name="password"]', "testpass123")
    page.click('button[type="submit"]')
    page.wait_for_url(f"{live_server}/dashboard/")
    return page

def test_create_task_flow(logged_in_admin: Page, live_server):
    # Navigate to tasks page (form is at the top)
    logged_in_admin.goto(f"{live_server}/tasks/")
    
    # Fill out the task details
    test_title = "Playwright Automation Task"
    # The form group labels are 'Title *', 'Assign To *'
    logged_in_admin.fill('input[name="title"]', test_title)
    logged_in_admin.select_option('select[name="assigned_to"]', index=1)
    logged_in_admin.fill('textarea[name="description"]', "Automation test description")
    
    # Submit the form using the 'Assign Task' button
    logged_in_admin.click('button:has-text("Assign Task")')
    
    # Verify redirect back to /tasks/ (it reloads the same page)
    expect(logged_in_admin).to_have_url(f"{live_server}/tasks/")
    
    # Assert task is listed in the table using text matching
    expect(logged_in_admin.locator("#taskTable")).to_contain_text(test_title)
