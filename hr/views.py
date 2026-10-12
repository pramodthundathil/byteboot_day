from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from decimal import Decimal
from django.utils import timezone
from hr.models import Employee, Attendance, LeaveType, LeaveRequest, PayrollPeriod, Payslip
from tenants.models import UserProfile, CompanyMembership, CustomRole
from tenants.decorators import require_permission

@login_required
@require_permission('hr', 'view')
def employee_list(request):
    company = request.company
    employees = Employee.objects.filter(company=company)
    return render(request, 'hr/employee_list.html', {
        'employees': employees,
        'active_tab': 'hr',
        'active_sub': 'employees'
    })


@login_required
@require_permission('hr', 'view')
def employee_detail(request, employee_id):
    company = request.company
    employee = get_object_or_404(Employee, id=employee_id, company=company)
    membership = CompanyMembership.objects.filter(user=employee.user, company=company).first() if employee.user else None
    leaves = LeaveRequest.objects.filter(employee=employee)
    
    perms = {}
    if membership:
        for mod in ['crm', 'invoicing', 'accounting', 'hr', 'projects']:
            perms[mod] = membership.has_permission(mod, 'view')

    return render(request, 'hr/employee_detail.html', {
        'employee': employee,
        'membership': membership,
        'perms': perms,
        'leaves': leaves,
        'active_tab': 'hr',
        'active_sub': 'employees'
    })


@login_required
@require_permission('hr', 'edit')
def employee_edit(request, employee_id):
    company = request.company
    employee = get_object_or_404(Employee, id=employee_id, company=company)
    custom_roles = CustomRole.objects.filter(company=company)
    membership = CompanyMembership.objects.filter(user=employee.user, company=company).first() if employee.user else None

    if request.method == 'POST':
        employee.first_name = request.POST.get('first_name', employee.first_name)
        employee.last_name = request.POST.get('last_name', employee.last_name)
        employee.email = request.POST.get('email', employee.email)
        employee.phone = request.POST.get('phone', employee.phone)
        employee.department = request.POST.get('department', employee.department)
        employee.designation = request.POST.get('designation', employee.designation)
        employee.status = request.POST.get('status', employee.status)
        employee.basic_salary = Decimal(request.POST.get('basic_salary', str(employee.basic_salary)))
        employee.hra = Decimal(request.POST.get('hra', str(employee.hra)))
        employee.allowance = Decimal(request.POST.get('allowance', str(employee.allowance)))
        employee.bank_name = request.POST.get('bank_name', employee.bank_name)
        employee.account_number = request.POST.get('account_number', employee.account_number)
        employee.save()

        # Update or Create User Account & Roles
        create_user = request.POST.get('create_user') == 'on'
        if create_user:
            username = request.POST.get('username') or employee.email.split('@')[0]
            password = request.POST.get('password')
            role = request.POST.get('role', 'STAFF')
            custom_role_id = request.POST.get('custom_role_id')

            user_obj = employee.user
            if not user_obj:
                user_obj, _ = User.objects.get_or_create(
                    username=username,
                    defaults={'email': employee.email, 'first_name': employee.first_name, 'last_name': employee.last_name}
                )
                employee.user = user_obj
                employee.save(update_fields=['user'])

            if password and password.strip():
                user_obj.set_password(password.strip())
                user_obj.save()

            profile, _ = UserProfile.objects.get_or_create(user=user_obj)
            if not profile.active_company:
                profile.active_company = company
                profile.save()

            mem, _ = CompanyMembership.objects.get_or_create(user=user_obj, company=company)
            
            role_choice = request.POST.get('role_choice', request.POST.get('role', 'DIRECT'))
            custom_role_id = request.POST.get('custom_role_id')

            if role_choice == 'COMPANY_ADMIN':
                mem.role = 'COMPANY_ADMIN'
                mem.custom_role = None
                mem.permissions = {}
            elif role_choice == 'CUSTOM' or (custom_role_id and custom_role_id.strip()):
                mem.role = 'CUSTOM'
                mem.custom_role = CustomRole.objects.filter(id=custom_role_id, company=company).first()
                mem.permissions = {}
            else:
                mem.role = 'STAFF'
                mem.custom_role = None
                
                # Build granular direct permission matrix for without-role employee assignment
                user_permissions = {}
                modules = ['crm', 'invoicing', 'accounting', 'hr', 'projects']
                actions = ['view', 'create', 'edit', 'delete', 'approve', 'assign', 'post', 'record_payment', 'convert']
                for mod in modules:
                    user_permissions[mod] = {}
                    for act in actions:
                        param = f"perm_{mod}_{act}"
                        user_permissions[mod][act] = (request.POST.get(param) == 'on' or request.POST.get(param) == '1')
                mem.permissions = user_permissions

            mem.is_active = (request.POST.get('user_active') == 'on' or 'user_active' in request.POST)
            mem.save()

        messages.success(request, f"Employee {employee.full_name} profile and system user access updated.")
        return redirect('hr:employee_detail', employee_id=employee.id)

    return render(request, 'hr/employee_form_edit.html', {
        'employee': employee,
        'membership': membership,
        'custom_roles': custom_roles,
        'active_tab': 'hr',
        'active_sub': 'employees'
    })


@login_required
def employee_create(request):
    company = request.company
    custom_roles = CustomRole.objects.filter(company=company)

    if request.method == 'POST':
        count = Employee.objects.filter(company=company).count() + 1
        emp_id = f"EMP-{100 + count}"

        first_name = request.POST.get('first_name')
        last_name = request.POST.get('last_name')
        email = request.POST.get('email')

        user_obj = None
        create_user = request.POST.get('create_user') == 'on'

        if create_user:
            username = request.POST.get('username') or email.split('@')[0]
            password = request.POST.get('password') or 'Byteboot@123'
            role = request.POST.get('role', 'STAFF')
            custom_role_id = request.POST.get('custom_role_id')

            user_obj, created = User.objects.get_or_create(
                username=username,
                defaults={'email': email, 'first_name': first_name, 'last_name': last_name}
            )
            if created or password:
                user_obj.set_password(password)
                user_obj.save()

            profile, _ = UserProfile.objects.get_or_create(user=user_obj)
            if not profile.active_company:
                profile.active_company = company
                profile.save()

            mem, _ = CompanyMembership.objects.get_or_create(
                user=user_obj,
                company=company,
                defaults={'role': role}
            )
            if role == 'CUSTOM' and custom_role_id:
                mem.custom_role = CustomRole.objects.filter(id=custom_role_id, company=company).first()
                mem.save()

        employee = Employee.objects.create(
            company=company,
            employee_id=emp_id,
            user=user_obj,
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=request.POST.get('phone', ''),
            department=request.POST.get('department', 'Engineering'),
            designation=request.POST.get('designation', 'Software Engineer'),
            joining_date=request.POST.get('joining_date', timezone.now().date()),
            basic_salary=Decimal(request.POST.get('basic_salary', '40000.00')),
            hra=Decimal(request.POST.get('hra', '16000.00')),
            allowance=Decimal(request.POST.get('allowance', '4000.00')),
            bank_name=request.POST.get('bank_name', 'HDFC Bank'),
            account_number=request.POST.get('account_number', '502000000123')
        )

        user_msg = f" with System User '{user_obj.username}' ({role})" if user_obj else " (No login user created)"
        messages.success(request, f"Employee {employee.employee_id} ({employee.full_name}) created{user_msg}.")
        return redirect('hr:employee_list')

    return render(request, 'hr/employee_form.html', {
        'custom_roles': custom_roles,
        'active_tab': 'hr',
        'active_sub': 'employees'
    })


@login_required
def employee_ajax_detail(request, employee_id):
    company = request.company
    employee = get_object_or_404(Employee, id=employee_id, company=company)
    membership = CompanyMembership.objects.filter(user=employee.user, company=company).first() if employee.user else None
    return render(request, 'hr/employee_drawer_partial.html', {
        'employee': employee,
        'membership': membership
    })


@login_required
def leave_list(request):
    company = request.company
    user_employee = Employee.objects.filter(company=company, user=request.user).first()
    
    is_hr_admin = (
        request.user.is_superuser or
        (hasattr(request, 'membership') and (
            request.membership.role in ('COMPANY_ADMIN', 'HR', 'PLATFORM_ADMIN') or
            request.membership.has_permission('hr', 'approve') or
            request.membership.has_permission('hr', 'edit')
        ))
    )

    if is_hr_admin:
        leaves = LeaveRequest.objects.filter(company=company)
    elif user_employee:
        leaves = LeaveRequest.objects.filter(company=company, employee=user_employee)
    else:
        leaves = LeaveRequest.objects.none()
    
    return render(request, 'hr/leave_list.html', {
        'leaves': leaves,
        'user_employee': user_employee,
        'is_hr_admin': is_hr_admin,
        'active_tab': 'hr',
        'active_sub': 'leaves'
    })


@login_required
def leave_request(request):
    company = request.company
    employees = Employee.objects.filter(company=company, status='ACTIVE')
    user_employee = Employee.objects.filter(company=company, user=request.user).first()
    
    is_hr_admin = (
        request.user.is_superuser or
        (hasattr(request, 'membership') and (
            request.membership.role in ('COMPANY_ADMIN', 'HR', 'PLATFORM_ADMIN') or
            request.membership.has_permission('hr', 'create') or
            request.membership.has_permission('hr', 'edit')
        ))
    )

    lt_casual, _ = LeaveType.objects.get_or_create(company=company, name='Casual Leave', defaults={'yearly_quota': 12})
    lt_sick, _ = LeaveType.objects.get_or_create(company=company, name='Sick Leave', defaults={'yearly_quota': 10})
    leave_types = LeaveType.objects.filter(company=company)

    if request.method == 'POST':
        emp_id = request.POST.get('employee_id')
        if not emp_id and user_employee:
            emp_id = user_employee.id
            
        employee = get_object_or_404(Employee, id=emp_id, company=company)
        lt_id = request.POST.get('leave_type_id')
        l_type = get_object_or_404(LeaveType, id=lt_id, company=company)

        start = request.POST.get('start_date')
        end = request.POST.get('end_date')

        req = LeaveRequest.objects.create(
            company=company,
            employee=employee,
            leave_type=l_type,
            start_date=start,
            end_date=end,
            duration_days=Decimal(request.POST.get('duration_days', '1.0')),
            reason=request.POST.get('reason', 'Personal Work'),
            status='PENDING'
        )

        messages.success(request, f"Leave application submitted for {employee.full_name}.")
        return redirect('hr:leave_list')

    return render(request, 'hr/leave_form.html', {
        'employees': employees,
        'user_employee': user_employee,
        'is_hr_admin': is_hr_admin,
        'leave_types': leave_types,
        'active_tab': 'hr',
        'active_sub': 'leaves'
    })


@login_required
def leave_edit(request, leave_id):
    company = request.company
    leave = get_object_or_404(LeaveRequest, id=leave_id, company=company)
    user_employee = Employee.objects.filter(company=company, user=request.user).first()

    is_owner = (user_employee and leave.employee_id == user_employee.id)
    is_hr_admin = (
        request.user.is_superuser or
        (hasattr(request, 'membership') and (
            request.membership.role in ('COMPANY_ADMIN', 'HR', 'PLATFORM_ADMIN') or
            request.membership.has_permission('hr', 'edit')
        ))
    )

    if not (is_owner or is_hr_admin):
        messages.error(request, "You do not have permission to edit this leave application.")
        return redirect('hr:leave_list')

    leave_types = LeaveType.objects.filter(company=company)

    if request.method == 'POST':
        lt_id = request.POST.get('leave_type_id')
        l_type = get_object_or_404(LeaveType, id=lt_id, company=company)

        leave.leave_type = l_type
        leave.start_date = request.POST.get('start_date')
        leave.end_date = request.POST.get('end_date')
        leave.duration_days = Decimal(request.POST.get('duration_days', '1.0'))
        leave.reason = request.POST.get('reason', leave.reason)
        
        if leave.status == 'CANCELLED':
            leave.status = 'PENDING'

        leave.save()
        messages.success(request, f"Leave request #{leave.id} updated successfully.")
        return redirect('hr:leave_list')

    return render(request, 'hr/leave_form_edit.html', {
        'leave': leave,
        'leave_types': leave_types,
        'active_tab': 'hr',
        'active_sub': 'leaves'
    })


@login_required
def leave_action(request, leave_id, action):
    company = request.company
    leave = get_object_or_404(LeaveRequest, id=leave_id, company=company)
    user_employee = Employee.objects.filter(company=company, user=request.user).first()

    is_owner = (user_employee and leave.employee_id == user_employee.id)
    is_hr_admin = (
        request.user.is_superuser or
        (hasattr(request, 'membership') and (
            request.membership.role in ('COMPANY_ADMIN', 'HR', 'PLATFORM_ADMIN') or
            request.membership.has_permission('hr', 'approve') or
            request.membership.has_permission('hr', 'edit')
        ))
    )

    if action == 'approve':
        if not is_hr_admin:
            messages.error(request, "Permission denied to approve leave applications.")
            return redirect('hr:leave_list')
        leave.status = 'APPROVED'
        leave.approver = request.user
        leave.approval_date = timezone.now()
        leave.save()
        messages.success(request, f"Leave request for {leave.employee.full_name} APPROVED.")
        
    elif action == 'reject':
        if not is_hr_admin:
            messages.error(request, "Permission denied to reject leave applications.")
            return redirect('hr:leave_list')
        leave.status = 'REJECTED'
        leave.approver = request.user
        leave.approval_date = timezone.now()
        leave.rejection_reason = request.POST.get('rejection_reason', 'Denied by Manager')
        leave.save()
        messages.warning(request, f"Leave request for {leave.employee.full_name} REJECTED.")
        
    elif action == 'revert':
        if not (is_owner or is_hr_admin):
            messages.error(request, "Permission denied to revert leave application status.")
            return redirect('hr:leave_list')
        leave.status = 'PENDING'
        leave.approver = None
        leave.approval_date = None
        leave.rejection_reason = ''
        leave.save()
        messages.info(request, f"Leave request for {leave.employee.full_name} reverted to PENDING status.")
        
    elif action == 'cancel':
        if not (is_owner or is_hr_admin):
            messages.error(request, "Permission denied to cancel leave application.")
            return redirect('hr:leave_list')
        leave.status = 'CANCELLED'
        leave.save()
        messages.info(request, f"Leave request for {leave.employee.full_name} CANCELLED.")

    elif action == 'delete':
        if not (is_owner or is_hr_admin):
            messages.error(request, "Permission denied to delete leave application.")
            return redirect('hr:leave_list')
        emp_name = leave.employee.full_name
        leave.delete()
        messages.success(request, f"Leave application for {emp_name} DELETED.")

    return redirect('hr:leave_list')

