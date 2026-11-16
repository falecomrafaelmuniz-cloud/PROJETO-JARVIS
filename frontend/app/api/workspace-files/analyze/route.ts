import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';
import {
  ChatMessage,
  appendNotebookChatMessage,
  getExpertById,
  getNotebookChat,
  getNotebookDir,
  getNotebookMetadata,
} from '@/lib/notebooks-storage';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

function getApiKey(): string {
  if (process.env.GOOGLE_API_KEY) return process.env.GOOGLE_API_KEY;
  if (process.env.GEMINI_API_KEY) return process.env.GEMINI_API_KEY;

  try {
    const envPath = path.resolve(process.cwd(), '..', '.env');
    if (fs.existsSync(envPath)) {
      const content = fs.readFileSync(envPath, 'utf-8');
      const match = content.match(/(?:GOOGLE_API_KEY|GEMINI_API_KEY)=([^\r\n]+)/);
      if (match && match[1]) {
        return match[1].trim();
      }
    }
  } catch {}

  return '';
}

const CANDIDATE_MODELS = [
  'gemini-3.5-flash-lite',
  'gemini-3.1-flash-lite',
  'gemini-flash-latest',
];

interface Part {
  text?: string;
  inline_data?: {
    mime_type: string;
    data: string;
  };
}

async function buscarWebRapida(query: string, maxResults = 3): Promise<string[]> {
  try {
    const res = await fetch('https://html.duckduckgo.com/html/', {
      method: 'POST',
      headers: {
        'User-Agent':
          'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: new URLSearchParams({ q: query }),
    });
    if (!res.ok) return [];
    const html = await res.text();
    const regex = /<a class="result__snippet[^>]*>([\s\S]*?)<\/a>/g;
    const snippets: string[] = [];
    let match: RegExpExecArray | null;
    while ((match = regex.exec(html)) !== null && snippets.length < maxResults) {
      const clean = match[1]
        .replace(/<[^>]+>/g, '')
        .replace(/&nbsp;/g, ' ')
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'")
        .replace(/&amp;/g, '&')
        .trim();
      if (clean && clean.length > 20) {
        snippets.push(clean);
      }
    }
    return snippets;
  } catch {
    return [];
  }
}

async function chamarGemini(parts: Part[], systemInstruction?: string): Promise<string> {
  const apiKey = getApiKey();
  if (!apiKey) {
    throw new Error('Chave de API do Google/Gemini não configurada.');
  }

  let ultimoErro: Error | null = null;

  for (const model of CANDIDATE_MODELS) {
    try {
      const url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${apiKey}`;

      const payload: {
        contents: Array<{ parts: Part[] }>;
        system_instruction?: { parts: Array<{ text: string }> };
        generationConfig?: { temperature: number; maxOutputTokens: number };
      } = {
        contents: [{ parts }],
        generationConfig: {
          temperature: 0.3,
          maxOutputTokens: 2500,
        },
      };

      if (systemInstruction) {
        payload.system_instruction = {
          parts: [{ text: systemInstruction }],
        };
      }

      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errText = await res.text();
        throw new Error(`Modelo ${model} retornou ${res.status}: ${errText}`);
      }

      const data = await res.json();
      const text = data?.candidates?.[0]?.content?.parts?.[0]?.text;
      if (text) {
        return text.trim();
      }
    } catch (err) {
      ultimoErro = err instanceof Error ? err : new Error(String(err));
      continue;
    }
  }

  throw ultimoErro || new Error('Não foi possível gerar análise com nenhum modelo Gemini.');
}

export async function POST(req: Request) {
  try {
    const body = await req.json();
    const {
      notebookId = 'geral',
      fileId,
      fileIds,
      action = 'analisar',
      customPrompt,
      expert,
    } = body;

    const targetIds: string[] = Array.isArray(fileIds) && fileIds.length > 0
      ? fileIds
      : fileId
        ? [fileId]
        : [];

    if (targetIds.length === 0) {
      return NextResponse.json({ ok: false, error: 'Nenhum arquivo selecionado para análise.' }, { status: 400 });
    }

    const notebookMeta = getNotebookMetadata(notebookId);
    const persona = getExpertById(expert || notebookMeta?.expert, notebookMeta?.name);

    const dir = getNotebookDir(notebookId);
    const isMultiFile = targetIds.length > 1;
    const parts: Part[] = [];
    const nomesValidos: string[] = [];

    // Processa cada arquivo adicionando ao prompt com marcadores explícitos
    for (let i = 0; i < targetIds.length; i++) {
      const safeId = path.basename(targetIds[i]);
      const filePath = path.join(dir, safeId);

      if (!fs.existsSync(filePath)) continue;

      let displayName = safeId;
      const match = safeId.match(/^[a-zA-Z0-9]{8}-(.+)$/);
      if (match) displayName = match[1];
      nomesValidos.push(displayName);

      const ext = path.extname(safeId).toLowerCase().replace('.', '');

      const isText = [
        'txt', 'md', 'csv', 'json', 'py', 'js', 'ts', 'tsx', 'jsx', 'html', 'css', 'sql', 'sh', 'bat', 'log', 'xml', 'yaml', 'yml'
      ].includes(ext);

      const isImage = ['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'].includes(ext);
      const isPdf = ext === 'pdf';
      const isAudio = ['mp3', 'wav', 'ogg', 'm4a'].includes(ext);

      parts.push({
        text: `\n=== DOCUMENTO ${i + 1} de ${targetIds.length}: "${displayName}" ===\n`,
      });

      if (isText) {
        const fileText = fs.readFileSync(filePath, 'utf-8');
        const truncatedText = fileText.length > 80000 ? fileText.substring(0, 80000) + '\n...[conteúdo truncado]' : fileText;
        parts.push({
          text: `\`\`\`${ext}\n${truncatedText}\n\`\`\``,
        });
      } else if (isImage) {
        const buffer = fs.readFileSync(filePath);
        const mime = ext === 'png' ? 'image/png' : ext === 'webp' ? 'image/webp' : 'image/jpeg';
        parts.push({
          inline_data: {
            mime_type: mime,
            data: buffer.toString('base64'),
          },
        });
      } else if (isPdf) {
        const buffer = fs.readFileSync(filePath);
        parts.push({
          inline_data: {
            mime_type: 'application/pdf',
            data: buffer.toString('base64'),
          },
        });
      } else if (isAudio) {
        const buffer = fs.readFileSync(filePath);
        const mime = ext === 'mp3' ? 'audio/mp3' : ext === 'wav' ? 'audio/wav' : 'audio/ogg';
        parts.push({
          inline_data: {
            mime_type: mime,
            data: buffer.toString('base64'),
          },
        });
      }
    }

    if (nomesValidos.length === 0) {
      return NextResponse.json({ ok: false, error: 'Nenhum dos arquivos selecionados foi encontrado no caderno.' }, { status: 404 });
    }

    // Histórico de Conversa Anterior (Memória contínua estilo NotebookLM)
    const existingChat = getNotebookChat(notebookId);
    if (existingChat.length > 0) {
      const ultimasMensagens = existingChat.slice(-6); // Últimas 6 mensagens
      const historicoFormatado = ultimasMensagens
        .map((m) => `${m.role === 'user' ? 'Usuário' : 'JARVIS'}: ${m.content}`)
        .join('\n\n');

      parts.push({
        text:
          `\n=== HISTÓRICO RECENTE DA CONVERSA NESTE CADERNO (MEMÓRIA CONTÍNUA) ===\n` +
          `${historicoFormatado}\n` +
          `=======================================================================\n`,
      });
    }

    // Busca Web Inteligente Sob Demanda
    let webSnippets: string[] = [];
    const promptTexto = typeof customPrompt === 'string' && customPrompt.trim() ? customPrompt.trim() : '';

    if (promptTexto) {
      const pLow = promptTexto.toLowerCase();
      const precisaBusca =
        pLow.includes('reajuste') ||
        pLow.includes('aluguel') ||
        pLow.includes('indice') ||
        pLow.includes('índice') ||
        pLow.includes('igp-m') ||
        pLow.includes('igpm') ||
        pLow.includes('ipca') ||
        pLow.includes('inpc') ||
        pLow.includes('selic') ||
        pLow.includes('cdi') ||
        pLow.includes('dolar') ||
        pLow.includes('dólar') ||
        pLow.includes('cotacao') ||
        pLow.includes('cotação') ||
        pLow.includes('pesquis') ||
        pLow.includes('procur') ||
        pLow.includes('busc') ||
        pLow.includes('internet') ||
        pLow.includes('google') ||
        pLow.includes('lei') ||
        pLow.includes('artigo') ||
        pLow.includes('cnpj') ||
        pLow.includes('valor hoje') ||
        pLow.includes('porcentagem') ||
        pLow.includes('taxa');

      if (precisaBusca) {
        let termoBusca = '';
        if (pLow.includes('reajuste') || pLow.includes('aluguel') || pLow.includes('igp') || pLow.includes('ipca')) {
          termoBusca = 'IGP-M IPCA acumulado 12 meses FGV hoje reajuste aluguel';
        } else if (pLow.includes('dolar') || pLow.includes('dólar') || pLow.includes('cotacao')) {
          termoBusca = 'cotação dólar hoje comercial Brasil';
        } else {
          termoBusca = promptTexto
            .replace(/[?!.,]/g, '')
            .replace(/\b(qual|quais|como|quanto|onde|quem|por|que|para|com|me|diga|dizer|calcular|calcule|de|do|da|dos|das|no|na|nos|nas)\b/gi, '')
            .trim()
            .slice(0, 80);
        }

        if (termoBusca) {
          webSnippets = await buscarWebRapida(termoBusca, 3);
        }
      }
    }

    if (webSnippets.length > 0) {
      parts.push({
        text:
          `\n=== DADOS ATUALIZADOS OBTIDOS EM TEMPO REAL NA WEB (GOOGLE/DUCKDUCKGO) ===\n` +
          webSnippets.map((s, idx) => `${idx + 1}. ${s}`).join('\n') +
          `\n========================================================================\n`,
      });
    }

    // Monta a instrução de acordo com o contexto (único ou conjunto)
    let instrucaoFinal = '';

    if (isMultiFile) {
      if (promptTexto) {
        instrucaoFinal =
          `Você é o assistente executivo JARVIS operando como Notebook de Inteligência (estilo NotebookLM). O usuário selecionou ${nomesValidos.length} fontes (${nomesValidos.join(', ')}).\n\n` +
          `ORDEM / PERGUNTA DO USUÁRIO:\n"${promptTexto}"\n\n` +
          `DIRETRIZ DE ANÁLISE CRUZADA:\n` +
          `- Analise TODOS os documentos selecionados em conjunto, cruzando as informações com atenção cirúrgica.\n` +
          `- Se a pergunta for um desdobramento do histórico recente da conversa, mantenha a continuidade lógica.\n` +
          `- Se um documento citar dados que dependem de outro (ex.: boleto com valor/data de vencimento e contrato/foto com o índice ou cláusulas de reajuste), conecte os pontos e faça o cálculo conclusivo completo.\n` +
          (webSnippets.length > 0
            ? `- ATENÇÃO: Utilize os dados da pesquisa web em tempo real fornecidos acima (ex.: porcentagem acumulada do IGP-M ou IPCA) para aplicar sobre o valor do boleto e calcular o valor final exato reajustado em reais!\n`
            : '') +
          `- Indique de forma clara as fontes de onde cada dado foi extraído (ex: [Fonte: Arquivo X]).`;
      } else if (action === 'tabela') {
        instrucaoFinal =
          `Você é o assistente JARVIS. Extraia e consolide todos os dados numéricos e tabelas das fontes selecionadas (${nomesValidos.join(', ')}) em uma tabela Markdown unificada e organizada.`;
      } else {
        instrucaoFinal =
          `Você é o assistente executivo JARVIS. Faça uma ANÁLISE CONJUNTA e CRUZADA dos ${nomesValidos.length} documentos selecionados (${nomesValidos.join(', ')}):\n` +
          `1. **Visão Geral Integrada**: Qual a relação entre estes documentos e o que eles representam juntos?\n` +
          `2. **Dados Cruzados & Pontos Críticos**: Conecte datas, valores, cláusulas ou dados complementares entre os arquivos.\n` +
          `3. **Conclusões e Recomendações**: Destaque prazos, pendências, valores finais ou ações imediatas necessárias.`;
      }
    } else {
      // Arquivo único
      const docName = nomesValidos[0];
      if (action === 'ocr' || action === 'extrair_texto') {
        instrucaoFinal = `Extraia e transcreva TODO o conteúdo textual do documento "${docName}" com máxima fidelidade.`;
      } else if (action === 'tabela') {
        instrucaoFinal = `Extraia as tabelas e dados numéricos do documento "${docName}" em formato Markdown.`;
      } else if (promptTexto) {
        instrucaoFinal =
          `Você é o JARVIS. Responda à pergunta do usuário sobre a fonte "${docName}":\n"${promptTexto}"\n` +
          (webSnippets.length > 0
            ? `Utilize também os dados pesquisados na web em tempo real acima para complementar ou realizar qualquer cálculo necessário com precisão.`
            : '');
      } else {
        instrucaoFinal =
          `Você é o JARVIS. Faça uma análise executiva completa da fonte "${docName}":\n` +
          `1. **Resumo Executivo** (2 a 3 frases).\n` +
          `2. **Pontos-Chave** (valores, datas, tópicos críticos).\n` +
          `3. **Conclusões ou Ações Recomendadas**.`;
      }
    }

    // Diretriz de Especialista / Persona Ativa
    const instrucaoEspecialista =
      `\n=== ESPECIALISTA ATIVO NO CADERNO ===\n` +
      `Papel: ${persona.name} (${persona.shortRole})\n` +
      `Diretriz de Atuação Profissional:\n${persona.systemPrompt}\n` +
      `======================================\n`;

    parts.push({
      text: `${instrucaoEspecialista}\n=== INSTRUÇÃO DE ANÁLISE ===\n${instrucaoFinal}`,
    });

    const resultado = await chamarGemini(parts, persona.systemPrompt);

    // Salva na persistência do chat do caderno
    const userMsg: ChatMessage = {
      id: `msg-${Date.now()}-u`,
      role: 'user',
      content: promptTexto || (action === 'tabela' ? 'Extrair tabelas consolidadas' : 'Analisar conjunto de fontes'),
      timestamp: new Date().toISOString(),
      sources: nomesValidos,
    };
    appendNotebookChatMessage(notebookId, userMsg);

    const assistantMsg: ChatMessage = {
      id: `msg-${Date.now()}-a`,
      role: 'assistant',
      content: resultado,
      timestamp: new Date().toISOString(),
      sources: nomesValidos,
      buscouWeb: webSnippets.length > 0,
      expert: persona.id,
      expertName: persona.name,
    };
    appendNotebookChatMessage(notebookId, assistantMsg);

    const updatedHistory = getNotebookChat(notebookId);

    return NextResponse.json({
      ok: true,
      notebookId,
      isMultiFile,
      arquivos: nomesValidos,
      action,
      buscouWeb: webSnippets.length > 0,
      expert: {
        id: persona.id,
        name: persona.name,
        shortRole: persona.shortRole,
        icon: persona.icon,
        badgeColor: persona.badgeColor,
      },
      resultado,
      chatHistory: updatedHistory,
    });
  } catch (error) {
    const msg = error instanceof Error ? error.message : 'Falha na análise por IA';
    return NextResponse.json({ ok: false, error: msg }, { status: 500 });
  }
}
