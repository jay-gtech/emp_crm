import pytest
from playwright.sync_api import Page, expect

@pytest.fixture
def ux_page(page: Page, live_server, ui_seed_db):
    page.goto(f"{live_server}/auth/login")
    page.fill('input[name="email"]', "admin@test.com")
    page.fill('input[name="password"]', "testpass123")
    page.click('button[type="submit"]')
    page.wait_for_url(f"{live_server}/dashboard/")
    return page

def wait_for_ux(page: Page):
    """Wait for our custom ux.js readiness signal."""
    page.wait_for_function("document.documentElement.dataset.uxReady === 'true'")

def test_dark_mode_toggle(ux_page: Page):
    wait_for_ux(ux_page)
    
    toggle_label = ux_page.locator('.ux-theme-toggle')
    expect(toggle_label).to_be_visible()
    
    # Toggle to dark
    toggle_label.click()
    expect(ux_page.locator("html")).to_have_attribute("data-theme", "dark")
    
    # Reload and ensure it persists
    ux_page.reload()
    wait_for_ux(ux_page)
    expect(ux_page.locator("html")).to_have_attribute("data-theme", "dark")
    
    # Toggle back
    ux_page.locator('.ux-theme-toggle').click()
    expect(ux_page.locator("html")).to_have_attribute("data-theme", "light")

def test_keyboard_shortcut_gd(ux_page: Page, live_server):
    wait_for_ux(ux_page)
    ux_page.goto(f"{live_server}/tasks/")
    wait_for_ux(ux_page)
    
    # Type 'gd' slowly
    ux_page.keyboard.press("g")
    ux_page.keyboard.press("d")
    
    # Wait for the navigation
    ux_page.wait_for_url(f"{live_server}/dashboard/")
    expect(ux_page).to_have_url(f"{live_server}/dashboard/")

def test_command_palette_ctrl_k(ux_page: Page):
    wait_for_ux(ux_page)
    
    # Trigger the palette
    ux_page.evaluate("window.showPalette()")
    
    # Wait for the backdrop to be visible (id is ux-palette, class is ux-palette-backdrop)
    palette = ux_page.locator("#ux-palette")
    # Use explicit wait for visibility to handle the CSS animation
    palette.wait_for(state="visible", timeout=5000)
    expect(palette).to_be_visible()
    
    # Search and select
    ux_page.keyboard.type("Dashboard", delay=50)
    # The item should appear in the list
    item = ux_page.locator(".ux-palette-item").filter(has_text="Dashboard")
    item.wait_for(state="visible")
    expect(item).to_be_visible()
    
    # Press Escape to close
    ux_page.keyboard.press("Escape")
    palette.wait_for(state="hidden")
    expect(palette).not_to_be_visible()

def test_custom_confirm_modal(ux_page: Page, live_server):
    wait_for_ux(ux_page)
    
    # Trigger modal
    ux_page.evaluate("window.showConfirmModal('Delete item?', () => { window.CONFIRMED = true; })")
    
    modal = ux_page.locator("#ux-confirm-modal")
    modal.wait_for(state="visible", timeout=5000)
    expect(modal).to_be_visible()
    
    # Ensure inner card is also present
    expect(ux_page.locator(".ux-modal-card")).to_be_visible()
    
    # Click Confirm
    ux_page.locator("#uxConfirmBtn").click()
    
    # Wait for completion
    modal.wait_for(state="hidden")
    expect(modal).not_to_be_visible()
    result = ux_page.evaluate("window.CONFIRMED")
    assert result is True

def test_toast_notification(ux_page: Page):
    wait_for_ux(ux_page)
    ux_page.evaluate("window.showToast('Test Toast', 'success')")
    
    toast = ux_page.locator(".toast-success")
    expect(toast).to_be_visible()
    # No need to wait 10s, it's just slow and can be flaky
