from django.contrib import admin

from .models import Membership, Message, Room, UserPresence


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    raw_id_fields = ['user']


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ['name', 'created_by', 'created_at', 'last_message_at']
    search_fields = ['name']
    raw_id_fields = ['created_by', 'image']
    inlines = [MembershipInline]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ['id', 'room', 'author', 'created', 'is_deleted']
    list_filter = ['is_deleted']
    raw_id_fields = ['room', 'author', 'reply_to']


@admin.register(UserPresence)
class UserPresenceAdmin(admin.ModelAdmin):
    list_display = ['user', 'connections', 'last_seen']
    raw_id_fields = ['user']
