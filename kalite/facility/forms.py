"""
"""
import copy
import re

from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import ugettext_lazy as _

from .models import FacilityUser, Facility, FacilityGroup
from securesync.devices.models import Zone
from fle_utils.django_utils import verify_raw_password
from kalite.i18n import get_installed_language_packs, get_language_name, get_default_language


class FacilityUserForm(forms.ModelForm):
    """This form is used for 1) signing up, 2) creating users, and 3) editing users.

    The views contain manual logic for processing passwords (hashing them, etc), so we use
    custom fields here for the "password" and "confirm password" fields.
    """
    password_first   = forms.CharField(widget=forms.PasswordInput, label=_("Password"))
    password_recheck = forms.CharField(widget=forms.PasswordInput, label=_("Confirm password"))
    default_language = forms.ChoiceField(label=_("Default Language"))
    warned           = forms.BooleanField(widget=forms.HiddenInput, required=False, initial=False)

    def __init__(self, facility, *args, **kwargs):
        super(FacilityUserForm, self).__init__(*args, **kwargs)

        self.fields["default_language"].choices = [(lang_code, get_language_name(lang_code)) for lang_code in get_installed_language_packs()]

        # Select the initial default language,
        #   but only if we're not in the process of updating it to something else.
        if not self.fields["default_language"].initial and "default_language" not in self.changed_data:
            self.fields["default_language"].initial = (self.instance and self.instance.default_language) or get_default_language()

        # Passwords only required on new, not on edit
        self.fields["password_first"].required = self.instance.pk == ""
        self.fields["password_recheck"].required = self.instance.pk == ""

        # Across POST and GET requests
        self.fields["group"].queryset = FacilityGroup.objects.filter(facility=facility)
        self.fields["zone_fallback"].initial = facility.get_zone()
        self.fields["facility"].initial = facility

    class Meta:
        model = FacilityUser
        # Note: must preserve order
        fields = ("facility", "group", "username", "first_name", "last_name", "password_first", "password_recheck", "default_language", "is_teacher", "zone_fallback", "warned")
        widgets = {
            "is_teacher": forms.HiddenInput(),
            "zone_fallback": forms.HiddenInput(),
            "warned": forms.HiddenInput(),
        }

    def clean_username(self):
        facility = self.cleaned_data.get('facility', "")
        username = self.cleaned_data.get('username', "")

        # check if given username is unique on both facility users and admins, whatever the casing
        username_taken = FacilityUser.objects.filter(username__iexact=username, facility=facility).count() > 0
        username_changed = not self.instance or self.instance.username != username
        if username_taken and username_changed:
            if self.fields["facility"].queryset and self.fields["facility"].queryset.count() > 1:
                error_message = _("A user with this username at this facility already exists. Please choose a new username (or select a different facility) and try again.")
            else:
                error_message = _("A user with this username already exists. Please choose a new username and try again.")
            raise forms.ValidationError(error_message)

        elif User.objects.filter(username__iexact=username).count() > 0:
            # Admin (django) user exists with the same name; we don't want overlap there!
            raise forms.ValidationError(_("The specified username is unavailable. Please choose a new username and try again."))

        return self.cleaned_data['username']

    def clean_password_first(self):
        password = self.cleaned_data.get('password_first', "")
        if (self.instance and not self.instance.password) or password:
            # No password set, or password is being reset
            verify_raw_password(password)
        return password

    def clean_password_recheck(self):
        password_first = self.cleaned_data.get('password_first', "")
        password_recheck = self.cleaned_data.get('password_recheck', "")

        if (self.instance and not self.instance.password) or password_first or password_recheck:
            # Only perform check on a new user or a password change
            if password_first != password_recheck:
                raise forms.ValidationError(_("The passwords didn't match. Please re-enter the passwords."))
        return self.cleaned_data['password_recheck']

    def clean(self):
        super(FacilityUserForm, self).clean()

        # Check the name combo; do it here so it's a general error.
        if not self.cleaned_data.get("warned", False):
            facility = self.cleaned_data.get("facility")
            zone = facility and facility.get_zone() or None
            users_with_same_name = FacilityUser.objects.filter(first_name=self.cleaned_data["first_name"], last_name=self.cleaned_data["last_name"]) \
                .filter(Q(signed_by__devicezone__zone=zone) | Q(zone_fallback=zone))  # within the same facility
            if users_with_same_name and (not self.instance or self.instance not in users_with_same_name):
                self.data = copy.deepcopy(self.data)
                self.data["warned"] = self.cleaned_data["warned"] = True
                msg = "%s %s" % (_("%(num_users)d user(s) with this name already exists (usernames=%(username_list)s).") % {
                    "num_users": users_with_same_name.count(),
                    "username_list": [user["username"] for user in users_with_same_name.values("username")],
                }, _("Please consider choosing another name, or re-submit to complete."))
                raise ValidationError(msg)  # general error, not associated with a field.
        return self.cleaned_data

class FacilityForm(forms.ModelForm):

    class Meta:
        model = Facility
        fields = ("name", "description", "address", "address_normalized", "latitude", "longitude", "zoom", "contact_name", "contact_phone", "contact_email", "user_count", "zone_fallback", )
        widgets = {
            "zone_fallback": forms.HiddenInput(),
        }

    def clean_user_count(self):
        user_count = self.cleaned_data['user_count']

        if user_count is not None and user_count < 1:
            raise ValidationError(_("User count should should be at least one."), code='invalid_user_count')

        return user_count

class FacilityGroupForm(forms.ModelForm):

    def __init__(self, facility, *args, **kwargs):
        super(FacilityGroupForm, self).__init__(*args, **kwargs)

        # Across POST and GET requests
        self.fields["zone_fallback"].initial = facility.get_zone()
        self.fields["facility"].initial = facility

    class Meta:
        model = FacilityGroup
        fields = ("name", "facility", "zone_fallback", )
        widgets = {
            "facility": forms.HiddenInput(),
            "zone_fallback": forms.HiddenInput(),
        }

    def clean_name(self):
        name = self.cleaned_data.get("name", "")
        ungrouped = re.compile("[uU]+ngrouped")

        if ungrouped.match(name):
            raise forms.ValidationError(_("This group name is reserved. Please choose one without 'ungrouped' in the name."))

        return name


class LoginForm(forms.ModelForm):
    password = forms.CharField(label=_("Password"), widget=forms.PasswordInput)
    callback_url = forms.CharField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = FacilityUser
        fields = ("facility", "username", "password")

    def __init__(self, request=None, *args, **kwargs):
        self.user_cache = None
        super(LoginForm, self).__init__(*args, **kwargs)
        self.fields['facility'].queryset = Facility.objects.all()
        if self.fields["facility"].queryset.count() < 2:
            self.fields["facility"].widget = forms.HiddenInput()

    def clean(self):
        # Don't call super; this isn't a proper where the model (FacilityUser) is being fully completed.

        username = self.cleaned_data.get('username', "")
        facility = self.cleaned_data.get('facility', "")
        password = self.cleaned_data.get('password', "")

        # Coerce
        users = FacilityUser.objects.filter(username__iexact=username, facility=facility)
        if users.count() == 1 and users[0].username != username:
            username = users[0].username
            self.cleaned_data['username'] = username

        try:
            self.user_cache = FacilityUser.objects.get(username=username, facility=facility)
        except FacilityUser.DoesNotExist as e:
            if self.fields["facility"].queryset.count() > 1:
                error_message = _("Username was not found for this facility. Did you type your username correctly, and choose the right facility?")
            else:
                error_message = _("Username was not found. Did you type your username correctly?")
            raise forms.ValidationError(error_message)

        if not self.user_cache.check_password(password):
            self.user_cache = None
            if password and "password" not in self._errors:
                self._errors["password"] = self.error_class([_("The passwords do not match. Please try again.")])

        return self.cleaned_data

    def get_user(self):
        return self.user_cache
