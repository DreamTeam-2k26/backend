from django.contrib import admin

from .models import Call, Participant


class ParticipantInline(admin.TabularInline):
    model = Participant
    extra = 0
    readonly_fields = ('user', 'joined_at', 'left_at', 'channel_name', 'state')
    can_delete = False


@admin.register(Call)
class CallAdmin(admin.ModelAdmin):
    list_display = ('title', 'project', 'status', 'scheduled_for', 'started_at', 'ended_at')
    list_filter = ('status',)
    search_fields = ('title', 'project__name')
    inlines = [ParticipantInline]
