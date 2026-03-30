from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.auth import login_required, role_required
from app.services.employee_service import (
    list_employees, get_employee, create_employee, update_employee,
    deactivate_employee, list_departments, EmployeeError,
)

router = APIRouter(prefix="/employees", tags=["employees"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def employee_list(
    request: Request,
    department: str | None = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    employees = list_employees(db, department=department)
    departments = list_departments(db)
    return templates.TemplateResponse(
        "employees/list.html",
        {
            "request": request,
            "employees": employees,
            "departments": departments,
            "selected_dept": department,
            "current_user": current_user,
        },
    )


@router.get("/new", response_class=HTMLResponse)
def new_employee_form(
    request: Request,
    current_user: dict = Depends(role_required("admin")),
):
    return templates.TemplateResponse(
        "employees/form.html",
        {"request": request, "current_user": current_user, "employee": None, "error": None},
    )


@router.post("/new")
def create_employee_post(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    department: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(role_required("admin")),
):
    try:
        create_employee(db, name, email.strip().lower(), password, role, department or None)
        return RedirectResponse("/employees/", status_code=302)
    except EmployeeError as e:
        return templates.TemplateResponse(
            "employees/form.html",
            {"request": request, "current_user": current_user, "employee": None, "error": str(e)},
            status_code=400,
        )


@router.get("/{employee_id}", response_class=HTMLResponse)
def employee_detail(
    employee_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    try:
        employee = get_employee(db, employee_id)
    except EmployeeError:
        return RedirectResponse("/employees/", status_code=302)
    return templates.TemplateResponse(
        "employees/detail.html",
        {"request": request, "employee": employee, "current_user": current_user},
    )


@router.post("/{employee_id}/edit")
def edit_employee(
    employee_id: int,
    request: Request,
    name: str = Form(...),
    department: str = Form(""),
    role: str = Form(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(role_required("admin")),
):
    try:
        update_employee(db, employee_id, name=name, department=department or None, role=role)
        return RedirectResponse(f"/employees/{employee_id}", status_code=302)
    except EmployeeError as e:
        employee = get_employee(db, employee_id)
        return templates.TemplateResponse(
            "employees/detail.html",
            {"request": request, "employee": employee, "current_user": current_user, "error": str(e)},
            status_code=400,
        )


@router.post("/{employee_id}/deactivate")
def deactivate(
    employee_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(role_required("admin")),
):
    try:
        deactivate_employee(db, employee_id)
    except EmployeeError:
        pass
    return RedirectResponse("/employees/", status_code=302)
