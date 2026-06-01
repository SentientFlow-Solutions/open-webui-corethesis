import { WEBUI_API_BASE_URL } from '$lib/constants';

export type OpenclawAgent = {
	id: string;
	name: string;
	emoji?: string | null;
	model?: string | null;
	is_default?: boolean;
};

export type OpenclawHealth = {
	ok: boolean;
	binary: string;
	version?: string | null;
	error?: string | null;
};

export type OpenclawChatRequest = {
	message: string;
	agent?: string;
	session_key?: string;
	chat_id?: string;
	thinking?: 'off' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'adaptive' | 'max';
	timeout?: number;
};

export type OpenclawChatResponse = {
	reply: string;
	agent: string;
	session_key: string;
	raw?: Record<string, unknown> | null;
};

const handle = async (res: Response) => {
	if (!res.ok) {
		let detail = res.statusText;
		try {
			const body = await res.json();
			detail = body?.detail ?? detail;
		} catch {
			/* ignore */
		}
		throw new Error(detail || `Request failed: ${res.status}`);
	}
	return res.json();
};

export const getOpenclawHealth = async (token: string): Promise<OpenclawHealth> => {
	return fetch(`${WEBUI_API_BASE_URL}/openclaw/health`, {
		headers: { authorization: `Bearer ${token}` }
	}).then(handle);
};

export const listOpenclawAgents = async (token: string): Promise<OpenclawAgent[]> => {
	return fetch(`${WEBUI_API_BASE_URL}/openclaw/agents`, {
		headers: { authorization: `Bearer ${token}` }
	}).then(handle);
};

export const sendOpenclawMessage = async (
	token: string,
	body: OpenclawChatRequest
): Promise<OpenclawChatResponse> => {
	return fetch(`${WEBUI_API_BASE_URL}/openclaw/chat`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify(body)
	}).then(handle);
};
