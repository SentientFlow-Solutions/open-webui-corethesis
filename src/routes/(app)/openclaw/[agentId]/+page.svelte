<script>
	import { onMount, tick } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { mobile, showSidebar, user } from '$lib/stores';
	import {
		listOpenclawAgents,
		sendOpenclawMessage
	} from '$lib/apis/openclaw';
	import Sidebar from '$lib/components/icons/Sidebar.svelte';

	$: agentId = $page.params.agentId;

	let agent = null;
	let agents = [];
	let messages = [];
	let input = '';
	let sending = false;
	let errorMessage = '';
	let chatId = '';
	let messagesEl;

	const chatStorageKey = (agentId) => `openclaw:chat:${agentId}`;

	const loadAgents = async () => {
		try {
			agents = await listOpenclawAgents(localStorage.token);
			agent = agents.find((a) => a.id === agentId) ?? null;
		} catch (e) {
			errorMessage = e?.message ?? String(e);
		}
	};

	const restoreOrCreateChat = () => {
		const raw = localStorage.getItem(chatStorageKey(agentId));
		if (raw) {
			try {
				const parsed = JSON.parse(raw);
				if (parsed?.chatId && Array.isArray(parsed?.messages)) {
					chatId = parsed.chatId;
					messages = parsed.messages;
					return;
				}
			} catch {
				/* ignore */
			}
		}
		chatId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
		messages = [];
		persist();
	};

	const persist = () => {
		try {
			localStorage.setItem(
				chatStorageKey(agentId),
				JSON.stringify({ chatId, messages })
			);
		} catch {
			/* quota / private mode — drop silently */
		}
	};

	const newSession = () => {
		chatId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
		messages = [];
		persist();
	};

	const scrollToBottom = async () => {
		await tick();
		if (messagesEl) {
			messagesEl.scrollTop = messagesEl.scrollHeight;
		}
	};

	const send = async () => {
		const text = input.trim();
		if (!text || sending) return;
		errorMessage = '';
		input = '';
		messages = [...messages, { role: 'user', content: text, ts: Date.now() }];
		persist();
		scrollToBottom();

		sending = true;
		try {
			const res = await sendOpenclawMessage(localStorage.token, {
				message: text,
				agent: agentId,
				chat_id: chatId
			});
			messages = [
				...messages,
				{
					role: 'assistant',
					content: res.reply,
					ts: Date.now(),
					session_key: res.session_key
				}
			];
			persist();
		} catch (e) {
			errorMessage = e?.message ?? String(e);
			messages = [
				...messages,
				{
					role: 'assistant',
					content: `*(gateway error — see banner above)*`,
					ts: Date.now(),
					error: true
				}
			];
			persist();
		} finally {
			sending = false;
			scrollToBottom();
		}
	};

	const onKeydown = (e) => {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			send();
		}
	};

	$: if (agentId) {
		restoreOrCreateChat();
	}

	onMount(loadAgents);
</script>

<svelte:head>
	<title>OpenClaw — {agent?.name ?? agentId}</title>
</svelte:head>

<div
	class="flex flex-col w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: ''} max-w-full"
>
	<nav class="px-2 pt-1.5 backdrop-blur-xl w-full drag-region">
		<div class="flex items-center gap-2">
			{#if $mobile}
				<button
					class="cursor-pointer p-1.5 flex rounded-xl hover:bg-gray-50 dark:hover:bg-gray-850 transition"
					on:click={() => showSidebar.set(!$showSidebar)}
				>
					<div class="self-center">
						<Sidebar className="size-5" />
					</div>
				</button>
			{/if}
			<button
				class="text-xs text-gray-500 hover:text-gray-800 dark:hover:text-gray-200 px-2 py-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-850 transition"
				on:click={() => goto('/openclaw')}
			>
				← Agents
			</button>
			<div class="flex items-center gap-2 px-2 py-1">
				<span class="text-xl">{agent?.emoji ?? '🤖'}</span>
				<span class="font-medium">{agent?.name ?? agentId}</span>
				{#if agent?.model}
					<span class="text-xs text-gray-500">· {agent.model}</span>
				{/if}
			</div>
			<div class="flex-1"></div>
			<button
				class="text-xs text-gray-500 hover:text-gray-800 dark:hover:text-gray-200 px-2 py-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-850 transition"
				on:click={newSession}
				title="Start a fresh session — clears local history and gives the agent a new session-key."
			>
				New session
			</button>
		</div>
	</nav>

	<div class="flex-1 overflow-y-auto px-4 sm:px-6 py-4" bind:this={messagesEl}>
		<div class="max-w-3xl mx-auto space-y-4">
			{#if errorMessage}
				<div
					class="rounded-xl border border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950/40 px-4 py-2 text-sm text-red-800 dark:text-red-200"
				>
					{errorMessage}
				</div>
			{/if}

			{#if messages.length === 0}
				<div class="text-sm text-gray-500 dark:text-gray-400 text-center pt-10">
					Send a message to start. The gateway keeps conversation context per session.
				</div>
			{/if}

			{#each messages as msg, i (i)}
				<div class={msg.role === 'user' ? 'flex justify-end' : 'flex justify-start'}>
					<div
						class={(msg.role === 'user'
							? 'bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-gray-100'
							: msg.error
								? 'bg-red-50 dark:bg-red-950/40 text-red-800 dark:text-red-200 border border-red-200 dark:border-red-800'
								: 'bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 border border-gray-200 dark:border-gray-800') +
							' rounded-2xl px-4 py-3 max-w-[85%] whitespace-pre-wrap break-words text-sm'}
					>
						{msg.content}
					</div>
				</div>
			{/each}

			{#if sending}
				<div class="flex justify-start">
					<div
						class="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-2xl px-4 py-3 text-sm text-gray-500"
					>
						Thinking…
					</div>
				</div>
			{/if}
		</div>
	</div>

	<div class="px-3 sm:px-6 pb-4">
		<div class="max-w-3xl mx-auto">
			<div
				class="flex items-end gap-2 rounded-2xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 px-3 py-2"
			>
				<textarea
					bind:value={input}
					on:keydown={onKeydown}
					placeholder="Message {agent?.name ?? agentId}…"
					rows="1"
					class="flex-1 resize-none bg-transparent outline-none text-sm py-1.5 max-h-40"
					disabled={sending}
				></textarea>
				<button
					class="rounded-xl bg-gray-900 dark:bg-gray-100 text-white dark:text-gray-900 text-sm font-medium px-3 py-1.5 disabled:opacity-40 disabled:cursor-not-allowed"
					on:click={send}
					disabled={sending || !input.trim()}
				>
					Send
				</button>
			</div>
			<div class="text-[11px] text-gray-400 mt-1 text-center">
				Enter to send · Shift+Enter for newline · Session id: <code>{chatId || '—'}</code>
			</div>
		</div>
	</div>
</div>
