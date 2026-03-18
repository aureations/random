from __future__ import annotations

import io
import traceback
import typing as t
import zlib
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

__all__: tuple[str, ...] = ("AutoResponder", "BaseLayoutView")


def _string_to_field_id(name: str) -> int:
    """Convert a string to a stable component ID"""
    return zlib.crc32(name.encode()) & 0x7FFFFFFF


class BaseLayoutView(discord.ui.LayoutView):
    """Base view with all safety features implemented"""
    interaction: discord.Interaction | None = None
    message: discord.Message | None = None

    def __init__(self, user: discord.User | discord.Member, timeout: float = 60.0):
        super().__init__(timeout=timeout)
        self.user = user

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "This interaction is not for you.", 
                ephemeral=True
            )
            return False
        self.interaction = interaction
        return True

    def _disable_all(self) -> None:
        for item in self.walk_children():
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True

    async def _edit(self, *args: t.Any, **kwargs: t.Any) -> None:
        if self.interaction is None and self.message is not None:
            await self.message.edit(*args, **kwargs)
        elif self.interaction is not None:
            try:
                await self.interaction.response.edit_message(*args, **kwargs)
            except discord.InteractionResponded:
                self.message = await self.interaction.original_response()
                await self.message.edit(*args, **kwargs)

    async def on_error(
        self, 
        interaction: discord.Interaction, 
        error: Exception, 
        item: discord.ui.Item[BaseLayoutView]
    ) -> None:
        tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        error_msg = f"An error occurred while processing the interaction for {str(item)}:\n```py\n{tb}\n```"
        
        self._disable_all()
        self.add_item(discord.ui.TextDisplay(error_msg))
        await self._edit(view=self)
        self.stop()

    async def on_timeout(self) -> None:
        self._disable_all()
        await self._edit(view=self)


class TriggerListActionRow(discord.ui.ActionRow["AutoResponderView"]):
    """Action row for navigation in the list view"""
    view: "AutoResponderView"

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button["AutoResponderView"]) -> None:
        if self.view.page > 0:
            self.view.page -= 1
            await self.view.update_list_display(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button["AutoResponderView"]) -> None:
        if (self.view.page + 1) * 5 < len(self.view.triggers):
            self.view.page += 1
            await self.view.update_list_display(interaction)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button["AutoResponderView"]) -> None:
        await self.view.update_list_display(interaction)


class AutoResponderConfirmRow(discord.ui.ActionRow["AutoResponderView"]):
    """Action row for delete confirmation"""
    view: "AutoResponderView"

    @discord.ui.button(label="Confirm Delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button["AutoResponderView"]) -> None:
        await self.view.execute_delete(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button["AutoResponderView"]) -> None:
        self.view._disable_all()
        await self.view._edit(view=self.view)
        self.view.stop()


class AutoResponderView(BaseLayoutView):
    """Main view for autoresponder operations"""
    
    TRIGGER_DISPLAY_ID = _string_to_field_id("trigger_display")
    LIST_DISPLAY_ID = _string_to_field_id("list_display")
    COUNT_DISPLAY_ID = _string_to_field_id("count_display")

    def __init__(
        self, 
        bot: commands.Bot,
        user: discord.User | discord.Member,
        guild_id: int,
        mode: str,
        trigger: str | None = None,
        response: str | None = None,
        timeout: float = 120.0
    ):
        super().__init__(user, timeout)
        self.bot = bot
        self.guild_id = guild_id
        self.mode = mode  # 'add', 'delete', 'list', 'confirm_delete'
        self.trigger = trigger
        self.response = response
        self.page = 0
        self.triggers: list[tuple[str, str, int]] = []  # (trigger, response, author_id)
        
        # Build the container based on mode
        self._build_container()

    def _build_container(self) -> None:
        """Build the container based on current mode"""
        components: list[discord.ui.Item] = []
        
        # Title section with bot thumbnail
        title_section = discord.ui.Section["AutoResponderView"](
            "## AutoResponder",
            accessory=discord.ui.Thumbnail["AutoResponderView"](self.bot.user.display_avatar.url),
        )
        components.append(title_section)
        
        # Mode-specific content
        if self.mode == "add":
            components.extend(self._build_add_content())
        elif self.mode == "delete":
            components.extend(self._build_delete_content())
        elif self.mode == "list":
            components.extend(self._build_list_content())
        elif self.mode == "confirm_delete":
            components.extend(self._build_confirm_delete_content())
        elif self.mode == "delete_complete":
            components.extend(self._build_delete_complete_content())
        
        # Add separator and footer
        components.append(discord.ui.Separator["AutoResponderView"](visible=True, spacing=discord.SeparatorSpacing.large))
        components.append(
            discord.ui.TextDisplay["AutoResponderView"](
                f"-# Requested by {self.user.display_name} • {discord.utils.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
        )
        
        # Create and add the container
        container = discord.ui.Container["AutoResponderView"](
            *components,
            accent_color=discord.Color.dark_grey(),
        )
        self.add_item(container)

    def _build_add_content(self) -> list[discord.ui.Item]:
        """Build content for add mode"""
        return [
            discord.ui.TextDisplay["AutoResponderView"](
                f"### Trigger\n```\n{self.trigger}\n```",
                id=self.TRIGGER_DISPLAY_ID,
            ),
            discord.ui.Separator["AutoResponderView"](visible=False, spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay["AutoResponderView"](
                f"### Response\n{self.response}",
            ),
            discord.ui.Separator["AutoResponderView"](visible=False, spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay["AutoResponderView"](
                "AutoResponder has been added successfully.",
            ),
        ]

    def _build_delete_content(self) -> list[discord.ui.Item]:
        """Build content for delete mode - trigger input"""
        return [
            discord.ui.TextDisplay["AutoResponderView"](
                "### Delete Trigger\nEnter the trigger phrase to delete:",
            ),
            discord.ui.Separator["AutoResponderView"](visible=False, spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay["AutoResponderView"](
                f"Trigger: **{self.trigger}**" if self.trigger else "No trigger specified.",
                id=self.TRIGGER_DISPLAY_ID,
            ),
        ]

    def _build_confirm_delete_content(self) -> list[discord.ui.Item]:
        """Build content for delete confirmation"""
        return [
            discord.ui.TextDisplay["AutoResponderView"](
                f"### Confirm Deletion\nAre you sure you want to delete the trigger **{self.trigger}**?",
                id=self.TRIGGER_DISPLAY_ID,
            ),
            discord.ui.Separator["AutoResponderView"](visible=False, spacing=discord.SeparatorSpacing.small),
            AutoResponderConfirmRow(),
        ]

    def _build_delete_complete_content(self) -> list[discord.ui.Item]:
        """Build content for delete completion"""
        return [
            discord.ui.TextDisplay["AutoResponderView"](
                f"### Trigger Deleted\nTrigger **{self.trigger}** has been removed.",
                id=self.TRIGGER_DISPLAY_ID,
            ),
        ]

    def _build_list_content(self) -> list[discord.ui.Item]:
        """Build content for list mode"""
        start = self.page * 5
        end = start + 5
        page_triggers = self.triggers[start:end]
        
        if not self.triggers:
            content = "No autoresponders configured for this server."
        else:
            lines = []
            for trigger, response, author_id in page_triggers:
                author = self.bot.get_user(author_id)
                author_name = author.display_name if author else f"Unknown ({author_id})"
                lines.append(f"**Trigger:** `{trigger}`")
                lines.append(f"**Response:** {response[:50]}{'...' if len(response) > 50 else ''}")
                lines.append(f"**Added by:** {author_name}")
                lines.append("")  # Empty line between entries
            content = "### AutoResponder List\n\n" + "\n".join(lines)
        
        components = [
            discord.ui.TextDisplay["AutoResponderView"](
                content,
                id=self.LIST_DISPLAY_ID,
            ),
        ]
        
        if self.triggers:
            # Add pagination info
            total_pages = (len(self.triggers) + 4) // 5
            components.append(
                discord.ui.TextDisplay["AutoResponderView"](
                    f"Page {self.page + 1} of {total_pages} • {len(self.triggers)} total triggers",
                    id=self.COUNT_DISPLAY_ID,
                )
            )
            components.append(TriggerListActionRow())
        
        return components

    async def update_list_display(self, interaction: discord.Interaction) -> None:
        """Update the list display after pagination"""
        list_display = self.find_item(self.LIST_DISPLAY_ID)
        count_display = self.find_item(self.COUNT_DISPLAY_ID)
        
        if list_display and isinstance(list_display, discord.ui.TextDisplay):
            start = self.page * 5
            end = start + 5
            page_triggers = self.triggers[start:end]
            
            lines = []
            for trigger, response, author_id in page_triggers:
                author = self.bot.get_user(author_id)
                author_name = author.display_name if author else f"Unknown ({author_id})"
                lines.append(f"**Trigger:** `{trigger}`")
                lines.append(f"**Response:** {response[:50]}{'...' if len(response) > 50 else ''}")
                lines.append(f"**Added by:** {author_name}")
                lines.append("")
            
            list_display.content = "### AutoResponder List\n\n" + "\n".join(lines)
            
            if count_display and isinstance(count_display, discord.ui.TextDisplay):
                total_pages = (len(self.triggers) + 4) // 5
                count_display.content = f"Page {self.page + 1} of {total_pages} • {len(self.triggers)} total triggers"
            
            await self._edit(view=self)

    async def execute_delete(self, interaction: discord.Interaction) -> None:
        """Execute the delete after confirmation"""
        # Get the cog instance to modify the triggers dict
        cog = self.bot.get_cog("AutoResponder")
        if cog and self.guild_id in cog.triggers and self.trigger and self.trigger.lower() in cog.triggers[self.guild_id]:
            del cog.triggers[self.guild_id][self.trigger.lower()]
        
        self.mode = "delete_complete"
        self._disable_all()
        
        # Rebuild container for completion message
        self.clear_items()
        self._build_container()
        
        await self._edit(view=self)
        self.stop()


class AutoResponder(commands.Cog):
    """AutoResponder system - responds to configured triggers"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # In-memory storage: guild_id -> {trigger: (response, author_id)}
        self.triggers: dict[int, dict[str, tuple[str, int]]] = defaultdict(dict)

    @commands.group(name="autoresponder", aliases=["ar"], invoke_without_command=True)
    @commands.has_permissions(manage_messages=True)
    async def autoresponder(self, ctx: commands.Context, trigger: str, *, response: str) -> None:
        """Add an autoresponder trigger
        
        Usage:
        ,autoresponder hello Hello, {user}!"""
        
        if len(trigger) > 100:
            await ctx.send("Trigger must be 100 characters or less.")
            return
        
        if len(response) > 1000:
            await ctx.send("Response must be 1000 characters or less.")
            return
        
        # Store the trigger
        self.triggers[ctx.guild.id][trigger.lower()] = (response, ctx.author.id)
        
        # Create and send the view
        view = AutoResponderView(
            self.bot,
            ctx.author,
            ctx.guild.id,
            "add",
            trigger=trigger,
            response=response
        )
        view.message = await ctx.send(view=view)

    @autoresponder.command(name="delete")
    @commands.has_permissions(manage_messages=True)
    async def autoresponder_delete(self, ctx: commands.Context, *, trigger: str) -> None:
        """Delete an autoresponder trigger"""
        
        trigger_lower = trigger.lower()
        guild_triggers = self.triggers.get(ctx.guild.id, {})
        
        if trigger_lower not in guild_triggers:
            # Show delete confirmation with non-existent trigger
            view = AutoResponderView(
                self.bot,
                ctx.author,
                ctx.guild.id,
                "delete",
                trigger=trigger
            )
            view.message = await ctx.send(view=view)
            return
        
        # Show confirmation view
        view = AutoResponderView(
            self.bot,
            ctx.author,
            ctx.guild.id,
            "confirm_delete",
            trigger=trigger
        )
        view.message = await ctx.send(view=view)

    @autoresponder.command(name="list")
    @commands.has_permissions(manage_messages=True)
    async def autoresponder_list(self, ctx: commands.Context) -> None:
        """List all autoresponders in this server"""
        
        guild_triggers = self.triggers.get(ctx.guild.id, {})
        trigger_list = [(t, r[0], r[1]) for t, r in guild_triggers.items()]
        
        view = AutoResponderView(
            self.bot,
            ctx.author,
            ctx.guild.id,
            "list"
        )
        view.triggers = trigger_list
        view.message = await ctx.send(view=view)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Listen for messages and trigger autoresponders"""
        if message.author.bot:
            return
        
        if not message.guild:
            return
        
        guild_triggers = self.triggers.get(message.guild.id, {})
        if not guild_triggers:
            return
        
        content_lower = message.content.lower()
        
        for trigger, (response, author_id) in guild_triggers.items():
            if trigger in content_lower:
                # Process the response (replace {user} with mention)
                formatted_response = response.replace("{user}", message.author.mention)
                await message.channel.send(formatted_response)
                break  # Only trigger once per message


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoResponder(bot))
