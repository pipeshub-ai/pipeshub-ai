import { config } from "dotenv";
config(); // Load environment variables first

import { json, urlencoded, Request, Response } from "express";
import { connect } from "./utils/db";
import { getFromDatabase, saveToDatabase } from "./utils/conversation";
import {markdownToBlocks} from '@tryfabric/mack';
// import { getUserByEmail } from "./services/iam.service";
// import { authJwtGenerator } from "./utils/createJwt";
// import { jwtValidator } from "./middlewares/userAuthentication";

import axios from "axios";
// import FormData from "form-data";
import app from "./slackApp";
import receiver from "./receiver";
// import sendNotification from "./services/taskNotification";
import { ConfigService } from "../../../modules/tokens_manager/services/cm.service";
import {  slackJwtGenerator } from "../../../libs/utils/createJwt";

// Type definitions
// interface AuthenticatedRequest extends Request {
//   decodedToken?: any;
// }

interface CitationUrls {
  [key: string]: string;
}

interface CitationData {
  citationId: string;
  citationData: {
    content: string;
    metadata: {
      recordId: string;
      recordName: string;
      recordType: string;
      createdAt: string;
      departments: string[];
      categories: string[];
      webUrl?: string;
    };
    chunkIndex?: string;
  };
}

interface BotResponse {
  content: string;
  citations?: CitationData[];
  messageType: string;
}

interface ConversationData {
  conversation: {
    _id: string;
    messages: BotResponse[];
  };
}

// interface SearchRecord {
//   _id: string;
//   name: string;
//   appSpecificRecordType: Array<{ name: string }>;
//   departments: Array<{ name: string }>;
//   recordType: string;
//   status: string;
//   createdAt: string;
// }

// interface SearchResponse {
//   records: SearchRecord[];
//   fileRecords: Array<{ recordId: string; fileName: string; extension: string }>;
//   [key: number]: { content: string };
// }

// interface Module {
//   _id: string;
//   name: string;
// }

// interface Customer {
//   _id: string;
//   registeredName: string;
// }

// interface FormDataValues {
//   name: string;
//   modules: string;
//   dueDate?: string;
//   customerAccount: string;
//   richformId: string;
// }

function convertCitationsToHyperlinks(text: string, citationUrls: CitationUrls): string {
  return text.replace(/\[(\d+)\]/g, (match, citationNumber) => {
    const url = citationUrls[citationNumber];
    if (url) {
      // Use proper markdown link format instead of Slack's HTML-style
      return `[[${citationNumber}](${url})]`;
    }
    return match;
  });
}

// Function to convert citations to hyperlinks
function convertCitationsToHyperlinks2(text: string, citationUrls: CitationUrls): string {
  return text.replace(/\[(\d+)\]/g, (match, citationNumber) => {
    const url = citationUrls[citationNumber];
    if (url) {
      return `<${url}|[${citationNumber}]>`;
    }
    return match; // Keep original if no URL found
  });
}

// Helper functions
// async function fetchModules(accessToken: string): Promise<Module[]> {
//   const response = await axios.get(
//     `${process.env.QUESTIONNAIRE_BACKEND_URL}/api/v1/modules`,
//     {
//       headers: {
//         Authorization: `Bearer ${accessToken}`,
//       },
//     }
//   );
//   return response.data;
// }

// async function fetchCustomers(accessToken: string): Promise<Customer[]> {
//   const response = await axios.get(
//     `${process.env.QUESTIONNAIRE_BACKEND_URL}/api/v1/customers/organization`,
//     {
//       headers: {
//         Authorization: `Bearer ${accessToken}`,
//       },
//     }
//   );
//   return response.data;
// }

// Middleware setup
receiver.router.use(json());
receiver.router.use(urlencoded());

// Routes
receiver.router.get("/", (req: Request, res: Response) => {
  console.log(req);
  res.send("Running");
});

// receiver.router.post("/send", jwtValidator, async (req: AuthenticatedRequest, res: Response): Promise<void> => {
//   try {
//     const userEmail = req.decodedToken?.email;
//     if (!userEmail) {
//       res.status(400).send("Email not found in token");
//       return;
//     }

//     const result = await app.client.users.lookupByEmail({
//       email: userEmail,
//       token: process.env.BOT_TOKEN || '', 
//     });

//     if (!result.user?.id) {
//       res.status(400).send("User not found");
//       return;
//     }

//     const userId = result.user.id;

//     await app.client.chat.postMessage({
//       token: process.env.BOT_TOKEN || '',
//       channel: userId,
//       text: "Hello, Saketh!",
//     });

//     res.status(200).send("Notification sent successfully.");
//   } catch (error) {
//     console.error("Error sending notification:", error);
//     res.status(500).send("Failed to send notification.");
//   }
// });

receiver.router.post("slack/command", (req: Request, res: Response) => {
  console.log("request");
  if (req.body.type === "url_verification") {
    res.send({ challenge: req.body.challenge });
  } else {
    res.status(200).send();
  }
});

// receiver.router.post("/task", jwtValidator, async (req: AuthenticatedRequest, res: Response): Promise<void> => {
//   try {
//     const email = req.decodedToken?.email;
//     if (!email) {
//       res.status(400).send("Email not found in token");
//       return;
//     }

//     const { notificationType, payload } = req.body;

//     await sendNotification(email, notificationType, payload);

//     res.status(200).send("Notification sent successfully.");
//   } catch (error) {
//     console.log(error instanceof Error ? error.stack : "Unknown error");
//     console.error("Error sending notification:", error instanceof Error ? error.message : "Unknown error");
//     res.status(500).send("Failed to send notification.");
//   }
// });

// Slack app command handlers
// app.command('/pipeshub', async ({ command, ack, respond, client, logger }) => {
//   await ack();

//   const args = command.text.trim().split(' ');
//   const subCommand = args[0].toLowerCase();

//   // Handle help command
//   if (command.text.trim().toLowerCase() === 'help') {
//     await respond({
//       blocks: [
//         {
//           type: "section",
//           text: {
//             type: "mrkdwn",
//             text: "Here are the commands that I understand:"
//           }
//         },
//         {
//           type: "section",
//           text: {
//             type: "mrkdwn",
//             text: `• \`/pipeshub help\`: Show this help information
// - \`/pipeshub search <query>\`: Search in the Knowledgebase
// - \`/pipeshub qna <question>\`: Ask a question to QnA ChatBot`
//           }
//         }
//       ],
//       response_type: 'ephemeral'
//     });
//     return;
//   }

//   // Handle QnA command
//   if (subCommand === 'qna') {
//     const question = args.slice(1).join(' ');
    
//     if (!question) {
//       await respond({
//         text: "Please provide a question. Usage: `/pipeshub qna <your question>`",
//         response_type: 'ephemeral'
//       });
//       return;
//     }

//     const initialMessage = await client.chat.postMessage({
//       channel: command.channel_id,
//       text: `Chat: "${question}"`,
//     });
    
//     if (!initialMessage.ts) {
//       await respond({
//         text: "Failed to create initial message",
//         response_type: 'ephemeral'
//       });
//       return;
//     }

//     const loadingMessage = await client.chat.postMessage({
//       channel: command.channel_id,
//       thread_ts: initialMessage.ts, 
//       text: `Generating response... :hourglass_flowing_sand:`,
//     });

//     if (!loadingMessage.ts) {
//       await respond({
//         text: "Failed to create loading message",
//         response_type: 'ephemeral'
//       });
//       return;
//     }

//     const messageTs = loadingMessage.ts;

//     try {
//       const lookupResult = await client.users.info({
//         user: command.user_id,
//       });
      
//       if (!lookupResult.user?.profile?.email) {
//         await client.chat.update({
//           channel: command.channel_id,
//           ts: messageTs,
//           text: "Error: Unable to get user email. Please try again later.",
//         });
//         return;
//       }

//       const email = lookupResult.user.profile.email;
//       let result = await getUserByEmail(email);
      
//       if (result.statusCode !== 200) {
//         await client.chat.update({
//           channel: command.channel_id,
//           ts: messageTs,
//           text: "Error: Unable to authenticate user. Please try again later.",
//         });
//         return;
//       }

//       const user = result.data as any;
//       const accessToken = authJwtGenerator(
//         user.email,
//         user._id,
//         user.orgId,
//         user.fullName,
//         user.profileURL,
//         user.mobile,
//         user.slug
//       );

//       const response = await axios.post<ConversationData>(
//         `${process.env.QUESTIONNAIRE_BACKEND_URL}/api/v1/conversations/create`,
//         {
//           query: question,
//           conversationSource: "sales",
//         },
//         {
//           headers: {
//             Authorization: `Bearer ${accessToken}`,
//             "Content-Type": "application/json",
//           },
//         }
//       );

//       const botResponse = response.data.conversation.messages.find(
//         (message) => message.messageType === "bot_response"
//       );

//       const conversationId = response.data.conversation._id;

//       // Save conversation to database with command.ts as threadId
//       await saveToDatabase({ 
//         threadId: initialMessage.ts,  
//         conversationId, 
//         email
//       });

//       if (botResponse) {
//         const blocks: any[] = [
//           {
//             type: "section",
//             text: {
//               type: "mrkdwn",
//               text: `*ChatBot Response for "${question}"*`,
//             },
//           },
//           {
//             type: "divider",
//           },
//           {
//             type: "section",
//             text: {
//               type: "mrkdwn",
//               text: botResponse.content,
//             },
//           },
//         ];

//         if (botResponse.citations && botResponse.citations.length > 0) {
//           blocks.push({
//             type: "context",
//             elements: [
//               {
//                 type: "mrkdwn",
//                 text: "*Citations:*",
//               },
//             ],
//           });

//           botResponse.citations.forEach((citation, index) => {
//             blocks.push({
//               type: "context",
//               elements: [
//                 {
//                   type: "mrkdwn",
//                   text: `[${index + 1}] ${citation.citationData.content}`,
//                 },
//               ],
//             });

//             blocks.push({
//               type: "actions",
//               elements: [
//                 {
//                   type: "button",
//                   text: {
//                     type: "plain_text",
//                     text: "View Details",
//                     emoji: true,
//                   },
//                   value: JSON.stringify({
//                     citationId: citation.citationId,
//                     recordId: citation.citationData.metadata.recordId,
//                     recordName: citation.citationData.metadata.recordName,
//                     content: citation.citationData.content,
//                     metadata: {
//                       recordType: citation.citationData.metadata.recordType,
//                       createdAt: citation.citationData.metadata.createdAt,
//                       departments: citation.citationData.metadata.departments,
//                       categories: citation.citationData.metadata.categories,
//                     },
//                   }),
//                   action_id: `view_citation_details_${index}`,
//                 },
//               ],
//             });
//           });
//         }

//         // Update the loading message with the chatbot response
//         await client.chat.update({
//           channel: command.channel_id,
//           ts: messageTs,
//           thread_ts: initialMessage.ts, 
//           blocks: blocks,
//           text: `ChatBot Response for "${question}"`,
//         });
//       } else {
//         await client.chat.update({
//           channel: command.channel_id,
//           ts: messageTs,
//           thread_ts: initialMessage.ts, 
//           text: "No bot response found in the API result.",
//         });
//       }
//     } catch (error) {
//       logger.error("Error calling the Chat API:", error);

//       await client.chat.update({
//         channel: command.channel_id,
//         ts: messageTs,
//         thread_ts: initialMessage.ts,
//         text: "Something went wrong while fetching the chatbot response. Please try again later.",
//       });
//     }
//   }

//   // Handle search command
//   if (subCommand === 'search') {
//     const searchText = args.slice(1).join(' ');
    
//     if (!searchText) {
//       await respond({
//         text: "Please provide a search term. Usage: `/pipeshub search <term>`",
//         response_type: 'ephemeral'
//       });
//       return;
//     }

//     // Send loading message
//     const loadingMessage = await client.chat.postMessage({
//       channel: command.channel_id,
//       text: `Searching for "${searchText}"... :hourglass_flowing_sand:`,
//     });

//     if (!loadingMessage.ts) {
//       await respond({
//         text: "Failed to create loading message",
//         response_type: 'ephemeral'
//       });
//       return;
//     }

//     const messageTs = loadingMessage.ts;

//     try {
//       // Get user information
//       const lookupResult = await client.users.info({
//         user: command.user_id,
//       });
      
//       if (!lookupResult.user?.profile?.email) {
//         await client.chat.update({
//           channel: command.channel_id,
//           ts: messageTs,
//           text: "Error: Unable to get user email. Please try again later.",
//         });
//         return;
//       }

//       const email = lookupResult.user.profile.email;
//       let result = await getUserByEmail(email);

//       if (result.statusCode !== 200) {
//         await client.chat.update({
//           channel: command.channel_id,
//           ts: messageTs,
//           text: "Error: Unable to authenticate user. Please try again later.",
//         });
//         return;
//       }

//       const user = result.data as any;
//       const accessToken = authJwtGenerator(
//         user.email,
//         user._id,
//         user.orgId,
//         user.fullName,
//         user.profileURL,
//         user.mobile,
//         user.slug
//       );

//       // Call search API
//       const response = await axios.post<SearchResponse>(
//         `${process.env.QUESTIONNAIRE_BACKEND_URL}/api/v1/knowledgebase/search?topK=20`,
//         { searchtext: searchText },
//         {
//           headers: {
//             Authorization: `Bearer ${accessToken}`,
//             "Content-Type": "application/json",
//           },
//         }
//       );

//       const blocks: any[] = [
//         {
//           type: "section",
//           text: {
//             type: "mrkdwn",
//             text: `*Search Results for "${searchText}"*`,
//           },
//         },
//         {
//           type: "divider",
//         },
//       ];

//       response.data.records.forEach((record, index) => {
//         const content = response.data[index] ? (response.data as any)[index].content : "No content preview available";
        
//         blocks.push({
//           type: "section",
//           text: {
//             type: "mrkdwn",
//             text: `*${record.name}*\n${content.substring(0, 150)}${content.length > 150 ? '...' : ''}`,
//           },
//         });

//         const categories = record.appSpecificRecordType.map(type => type.name).join(", ");
//         const departments = record.departments.map(dept => dept.name).join(", ");
        
//         blocks.push({
//           type: "context",
//           elements: [
//             {
//               type: "mrkdwn",
//               text: `*Categories:* ${categories} | *Departments:* ${departments}`,
//             },
//           ],
//         });

//         blocks.push({
//           type: "actions",
//           elements: [
//             {
//               type: "button",
//               text: {
//                 type: "plain_text",
//                 text: "View Details",
//                 emoji: true,
//               },
//               value: JSON.stringify({
//                 recordId: record._id,
//                 recordName: record.name,
//                 content: content,
//                 metadata: {
//                   recordType: record.recordType,
//                   status: record.status,
//                   createdAt: record.createdAt,
//                   departments: record.departments,
//                   categories: record.appSpecificRecordType,
//                   fileInfo: response.data.fileRecords.find(f => f.recordId === record._id),
//                 },
//               }),
//               action_id: `view_search_result_${index}`,
//             },
//           ],
//         });

//         blocks.push({
//           type: "divider",
//         });
//       });

//       // Update the loading message with search results
//       await client.chat.update({
//         channel: command.channel_id,
//         ts: messageTs,
//         blocks: blocks,
//         text: `Search Results for "${searchText}"`,
//       });

//     } catch (error) {
//       logger.error("Error handling search command:", error);
      
//       await client.chat.update({
//         channel: command.channel_id,
//         ts: messageTs,
//         text: "Something went wrong while fetching the results. Please try again later.",
//       });
//     }
//   }

//   // If no valid command is matched
//   if (!['help', 'search', 'qna'].includes(subCommand)) {
//     await respond({
//       text: "Unknown command. Use `/pipeshub help` to see available commands.",
//       response_type: 'ephemeral'
//     });
//   }
// });

// Slack app event handlers
// app.event('app_mention', async ({ event, say, client }) => {
//   // Remove the app mention from the text to get just the query
//   if (event.bot_id) {
//     return;
//   }

//   // Check if files are attached to the message
//   if (event.files) {
//     const fileDetails = event.files.map((file: any) => `- ${file.name}`).join("\n");
//     const fileIds = event.files.map((file: any) => file.id);

//     // Post the message with the list of files and action buttons
//     await client.chat.postMessage({
//       channel: event.channel,
//       thread_ts: event.thread_ts || event.ts,
//       text: `Upload files:\n${fileDetails}`,
//       blocks: [
//         {
//           type: "section",
//           text: {
//             type: "mrkdwn",
//             text: `Upload the following files:\n${fileDetails}`,
//           },
//         },
//         {
//           type: "actions",
//           elements: [
//             {
//               type: "button",
//               text: {
//                 type: "plain_text",
//                 text: "Upload to QnA",
//                 emoji: true,
//               },
//               value: JSON.stringify({ fileIds, action: "upload_to_qna" }),
//               action_id: "upload_to_qna",
//               style: "primary",
//             },
//             {
//               type: "button",
//               text: {
//                 type: "plain_text",
//                 text: "Upload to Knowledgebase",
//                 emoji: true,
//               },
//               value: JSON.stringify({ fileIds, action: "upload_to_kb" }),
//               action_id: "upload_to_knowledgebase",
//               style: "primary",
//             },
//           ],
//         },
//       ],
//     });

//     return;
//   }

//   const text = event.text.replace(/<@[A-Z0-9]+>/, '').trim();
  
//   if (!text) return; // If no text after mention, do nothing

//   try {
//     const threadId = event.thread_ts || event.ts;

//     // Lookup user information
//     const lookupResult = await client.users.info({
//       user: event.user || '',
//     });
    
//     if (!lookupResult.user?.profile?.email) {
//       console.error("Failed to get user email");
//       await client.chat.postMessage({
//         channel: event.channel,
//         thread_ts: threadId,
//         text: `Error: Unable to get user email. Please try again later.`,
//       });
//       return;
//     }
    
//     const email = lookupResult.user.profile.email;
//     let result = await getUserByEmail(email);
    
//     if (result.statusCode !== 200) {
//       console.error(`Failed to fetch user: ${result.data}`);
//       await client.chat.postMessage({
//         channel: event.channel,
//         thread_ts: threadId,
//         text: `Error: Unable to fetch user details. Please try again later.`,
//       });
//       return;
//     }
    
//     const user = result.data as any;
//     const userId = user._id;
//     const userSlug = user.slug;
//     const fullName = user.fullName;
//     const mobile = user.mobile;
//     const orgId = user.orgId;
//     const profileURL = user.profileURL;
    
//     const accessToken = authJwtGenerator(
//       user.email,
//       userId,
//       orgId,
//       fullName,
//       profileURL,
//       mobile,
//       userSlug
//     );
    
//     // Check if threadId exists in the database
//     const conversation = await getFromDatabase(threadId, email);
    
//     if (conversation) {
//       // Send loading message
//       const loadingMessage = await client.chat.postMessage({
//         channel: event.channel,
//         thread_ts: threadId,
//         text: `Generating response... :hourglass_flowing_sand:`,
//       });
    
//       if (!loadingMessage.ts) {
//         await client.chat.postMessage({
//           channel: event.channel,
//           thread_ts: threadId,
//           text: "Error: Failed to create loading message",
//         });
//         return;
//       }

//       const messageTs = loadingMessage.ts;
    
//       try {
//         // Continue the chat in an existing conversation
//         const response = await axios.post<ConversationData>(
//           `${process.env.QUESTIONNAIRE_BACKEND_URL}/api/v1/conversations/${conversation}/messages`,
//           { query: text },
//           {
//             headers: {
//               Authorization: `Bearer ${accessToken}`,
//               "Content-Type": "application/json",
//             },
//           }
//         );
    
//         const botResponse = response.data.conversation.messages.find(
//           (message) => message.messageType === "bot_response"
//         );
    
//         if (botResponse) {
//           const blocks: any[] = [
//             {
//               type: "section",
//               text: {
//                 type: "mrkdwn",
//                 text: `*ChatBot Response*`,
//               },
//             },
//             {
//               type: "divider",
//             },
//             {
//               type: "section",
//               text: {
//                 type: "mrkdwn",
//                 text: botResponse.content,
//               },
//             },
//           ];
    
//           if (botResponse.citations && botResponse.citations.length > 0) {
//             blocks.push({
//               type: "context",
//               elements: [
//                 {
//                   type: "mrkdwn",
//                   text: "*Citations:*",
//                 },
//               ],
//             });
    
//             botResponse.citations.forEach((citation, index) => {
//               blocks.push({
//                 type: "context",
//                 elements: [
//                   {
//                     type: "mrkdwn",
//                     text: `[${index + 1}] ${citation.citationData.content}`,
//                   },
//                 ],
//               });
    
//               blocks.push({
//                 type: "actions",
//                 elements: [
//                   {
//                     type: "button",
//                     text: {
//                       type: "plain_text",
//                       text: "View Details",
//                       emoji: true,
//                     },
//                     value: JSON.stringify({
//                       citationId: citation.citationId,
//                       recordId: citation.citationData.metadata.recordId,
//                       recordName: citation.citationData.metadata.recordName,
//                       content: citation.citationData.content,
//                       metadata: {
//                         recordType: citation.citationData.metadata.recordType,
//                         createdAt: citation.citationData.metadata.createdAt,
//                         departments: citation.citationData.metadata.departments,
//                         categories: citation.citationData.metadata.categories,
//                       },
//                     }),
//                     action_id: `view_citation_details_${index}`,
//                   },
//                 ],
//               });
//             });
//           }
    
//           // Update the loading message with the chatbot response
//           await client.chat.update({
//             channel: event.channel,
//             ts: messageTs,
//             blocks: blocks,
//             text: `ChatBot Response`,
//           });
//         } else {
//           await client.chat.update({
//             channel: event.channel,
//             ts: messageTs,
//             text: "No bot response found in the API result.",
//           });
//         }
//       } catch (error) {
//         console.error("Error calling the Chat API:", error);
    
//         await client.chat.update({
//           channel: event.channel,
//           ts: messageTs,
//           text: "Something went wrong while fetching the chatbot response. Please try again later.",
//         });
//       }
//     } else {
//       // No existing conversation, show the initial options
//       const blocks: any[] = [
//         {
//           type: "section",
//           text: {
//             type: "mrkdwn",
//             text: `Search "${text}" In KnowledgeBase`,
//           },
//         },
//         {
//           type: "actions",
//           elements: [
//             {
//               type: "button",
//               text: {
//                 type: "plain_text",
//                 text: "Search",
//                 emoji: true,
//               },
//               style: "primary",
//               action_id: "search_button",
//               value: JSON.stringify({
//                 searchText: text,
//                 threadTs: threadId,
//               }),
//             },
//           ],
//         },
       
//         {
//           type: "actions",
//           elements: [
//             {
//               type: "button",
//               text: {
//                 type: "plain_text",
//                 text: "QnA ChatBot",
//                 emoji: true,
//               },
//               style: "primary",
//               action_id: "chat_button",
//               value: JSON.stringify({
//                 searchText: text,
//                 threadTs: threadId,
//               }),
//             },
//           ]
//         },
//       ];

//       await say({
//         blocks: blocks,
//         text: `Search "${text}"`,
//         thread_ts: threadId,
//       });
//     }
//   } catch (error) {
//     console.error("Error handling app mention:", error);
//   }
// });

// Slack app message handler
app.message(async ({ message, client, context }) => {
  // Type guard to ensure message has required properties
  if (!message || typeof message !== 'object') {
    return;
  }

  const typedMessage = message as {
    subtype?: string;
    bot_id?: string;
    user?: string;
    files?: unknown[];
    text?: string;
    thread_ts?: string;
    ts: string;
    channel?: string;
  };

  const typedClient = client as {
    botUserId?: string;
    users: {
      info: (params: { user: string }) => Promise<{
        user?: {
          profile?: {
            email?: string;
          };
        };
      }>;
    };
    chat: {
      postMessage: (params: {
        channel: string;
        thread_ts?: string;
        text: string;
      }) => Promise<{ ts?: string }>;
      update: (params: {
        channel: string;
        ts: string;
        blocks?: unknown[];
        text: string;
      }) => Promise<unknown>;
    };
  };

  const typedContext = context as {
    botUserId?: string;
  };

  if (
    typedMessage.subtype === "bot_message" ||
    typedMessage.bot_id ||
    typedMessage.user === typedContext.botUserId ||
    typedMessage.files ||
    typedMessage.text?.match(/<@[A-Z0-9]+>/)
  ) {
    return;
  }

  if ('text' in message && message.text?.includes(`<@${typedClient.botUserId}>`)) {
    return;
  }

  try {
    const threadId = typedMessage.thread_ts || typedMessage.ts;
    const lookupResult = await typedClient.users.info({
      user: typedMessage.user!,
    });
    
    if (!lookupResult.user?.profile?.email) {
      console.error("Failed to get user email");
      return;
    }
    
    const email = lookupResult.user.profile.email;
    const configService = ConfigService.getInstance();
    const accessToken = slackJwtGenerator(email, await configService.getScopedJwtSecret());
   
    const conversation = await getFromDatabase(threadId, email);
    
      // Send loading message
      const loadingMessage = await typedClient.chat.postMessage({
        channel: typedMessage.channel!,
        thread_ts: threadId,
        text: `Generating response... :hourglass_flowing_sand:`,
      });
    
      if (!loadingMessage.ts) {
        console.error("Failed to create loading message");
        return;
      }

      const messageTs = loadingMessage.ts;
    
      try {
        const url = conversation ? `${process.env.QUESTIONNAIRE_BACKEND_URL}/api/v1/conversations/internal/${conversation}/messages` : `${process.env.QUESTIONNAIRE_BACKEND_URL}/api/v1/conversations/internal/create`;
        const response = await axios.post<ConversationData>(
          url,
          { 
            query: typedMessage.text,
            chatMode: "quick"
          },
          {
            headers: {
              Authorization: `Bearer ${accessToken}`,
              "Content-Type": "application/json",
            },
          }
        );

    if(!conversation){
        const conversationId = response.data.conversation._id;
        await saveToDatabase({ threadId: threadId, conversationId, email });
      }
        const botResponses = response.data.conversation.messages;
        let botResponse = null;
        if (botResponses.length > 0) {
          botResponse = botResponses[botResponses.length - 1];
        }
         console.log(botResponse, "botResponseeeeeeeeeeeeeeeeeeeeeeee");
        if (botResponse && botResponse.messageType === "bot_response") {
          
          let citationUrls: CitationUrls = {};
          if (botResponse.citations && botResponse.citations.length > 0) {
            
            botResponse.citations.forEach((citation) => {
              let webUrl = citation.citationData.metadata.webUrl;
              let chunkIndex = citation.citationData.chunkIndex;
              if (!webUrl?.startsWith("https://")) {
                webUrl = (process.env.FRONTEND_PUBLIC_URL || '') + (webUrl || '');
              }
              if (chunkIndex) {
                citationUrls[chunkIndex] = webUrl || '';
              }
           
            });
        }
        let blocks = [];
        const originalContent = botResponse.content;
        try {
          const contentForMack = convertCitationsToHyperlinks(originalContent, citationUrls);
          blocks = await markdownToBlocks(contentForMack);
          for (const block of blocks) {
            // Todo: replace substring &#39; by apostrophe
            if (block.type === "section" && block.text?.type === "mrkdwn") {
              block.text.text = block.text.text.replace(/\&#39;/g, "'");
            }
          }
        } catch (error) {
          console.error("Error converting markdown to blocks:", error);
          const contentForFallback = convertCitationsToHyperlinks2(originalContent, citationUrls);
          blocks = [
            {
              type: "section",
              text: {
                type: "mrkdwn",
                text: contentForFallback,
              },
            },
          ];
        }
       await typedClient.chat.update({
        channel: typedMessage.channel!,
        ts: messageTs,
        blocks: blocks,
        text: `ChatBot Response`,
      });
      }
        else {
          await typedClient.chat.update({
            channel: typedMessage.channel!,
            ts: messageTs,
            text: "Something went wrong! Please try again later.",
          });
        }
      } catch (error) {
        console.error("Error calling the Chat API:", error);
        await typedClient.chat.update({
          channel: typedMessage.channel!,
          ts: messageTs,
          text: "Something went wrong! Please try again later.",
        });
      }
}catch (error) {
  console.error("Error handling message:", error); 
}
});

// Continue with more action handlers...
// app.action("upload_to_qna", async ({ ack, body, client, logger }) => {
//   await ack();
  
//   const { fileId } = JSON.parse((body as any).actions[0].value);
//   console.log(fileId, "fileId1")

//   try {
  
//     const lookupResult = await client.users.info({ user: (body as any).user.id });
//     const email = lookupResult.user?.profile?.email;
    
//     if (!email) {
//       throw new Error('Unable to get user email');
//     }
    
//     let userResult = await getUserByEmail(email);
  
//     if (userResult.statusCode !== 200) {
//       throw new Error('Unable to authenticate user');
//     }
  
//     const user = userResult.data as any;
//     const accessToken = authJwtGenerator(
//       user.email,
//       user._id,
//       user.orgId,
//       user.fullName,
//       user.profileURL,
//       user.mobile,
//       user.slug
//     );

    
//     const modules = await fetchModules(accessToken);
//     const customers = await fetchCustomers(accessToken);

//     const result = await client.views.open({
//       trigger_id: (body as any).trigger_id,
//       view: {
//         type: "modal",
//         callback_id: "qna_upload_modal",
//         title: {
//           type: "plain_text",
//           text: "Upload to QnA"
//         },
//         submit: {
//           type: "plain_text",
//           text: "Submit"
//         },
//         close: {
//           type: "plain_text",
//           text: "Cancel"
//         },
//         blocks: [
//           {
//             type: "input",
//             block_id: "name_block",
//             element: {
//               type: "plain_text_input",
//               action_id: "name_input"
//             },
//             label: {
//               type: "plain_text",
//               text: "Name"
//             }
//           },
//           {
//             type: "input",
//             block_id: "modules_block",
//             element: {
//               type: "multi_static_select",
//               action_id: "modules_input",
//               options: modules.map(module => ({
//                 text: {
//                   type: "plain_text",
//                   text: module.name
//                 },
//                 value: module._id
//               }))
//             },
//             label: {
//               type: "plain_text",
//               text: "Modules"
//             }
//           },
//           {
//             type: "input",
//             block_id: "due_date_block",
//             optional: true,
//             element: {
//               type: "datepicker",
//               action_id: "due_date_input"
//             },
//             label: {
//               type: "plain_text",
//               text: "Due Date (Optional)"
//             }
//           },
//           {
//             type: "input",
//             block_id: "customer_account_block",
//             element: {
//               type: "static_select",
//               action_id: "customer_account_input",
//               options: customers.map(customer => ({
//                 text: {
//                   type: "plain_text",
//                   text: customer.registeredName
//                 },
//                 value: customer._id
//               }))
//             },
//             label: {
//               type: "plain_text",
//               text: "Customer Account"
//             }
//           }
//         ],
//         private_metadata: JSON.stringify({ fileId })
//       }
//     });
//     logger.info(result);
//   } catch (error) {
//     logger.error(error);
//     await client.chat.postMessage({
//       channel: (body as any).user.id,
//       text: "An error occurred while opening the upload form. Please try again later.",
//     });
//   }
// });

// Continue with more action handlers...
// app.view("qna_upload_modal", async ({ ack, body, view, client, logger }) => {
//   await ack();

//   const { fileId } = JSON.parse(view.private_metadata);
//   const values = view.state.values;

 
//   const loadingMessage = await client.chat.postMessage({
//     channel: (body as any).user.id,
//     text: `Uploading to QnA workflow... :hourglass_flowing_sand:`,
//   });

//   if (!loadingMessage.ts) {
//     await client.chat.postMessage({
//       channel: (body as any).user.id,
//       text: "Error: Failed to create loading message",
//     });
//     return;
//   }

//   const messageTs = loadingMessage.ts;

//   const formData: FormDataValues = {
//     name: values.name_block.name_input.value,
//     modules: JSON.stringify(
//       values.modules_block.modules_input.selected_options.map(
//         (option: any) => option.value
//       )
//     ),
//     dueDate: values.due_date_block.due_date_input?.selected_date 
//       ? new Date(values.due_date_block.due_date_input.selected_date).getTime().toString()
//       : undefined,
//     customerAccount: values.customer_account_block.customer_account_input.selected_option.value,
//     richformId: "67211ab5debf199cae5f13b7"
//   };

//   try {
    
//     const lookupResult = await client.users.info({
//       user: (body as any).user.id,
//     });
    
//     const email = lookupResult.user?.profile?.email;
//     if (!email) {
//       await client.chat.update({
//         channel: (body as any).user.id,
//         ts: messageTs,
//         text: "Error: Unable to get user email. Please try again later. :x:",
//       });
//       return;
//     }
    
//     let result = await getUserByEmail(email);

//     if (result.statusCode !== 200) {
//       await client.chat.update({
//         channel: (body as any).user.id,
//         ts: messageTs,
//         text: "Error: Unable to authenticate user. Please try again later. :x:",
//       });
//       return;
//     }

//     const user = result.data as any;
//     const accessToken = authJwtGenerator(
//       user.email,
//       user._id,
//       user.orgId,
//       user.fullName,
//       user.profileURL,
//       user.mobile,
//       user.slug
//     );

    
//     const fileInfo = await client.files.info({
//       file: fileId
//     });

//     if (!fileInfo.file?.url_private) {
//       throw new Error('File not found or inaccessible');
//     }

// const fileContent = await axios({
//   method: 'get',
//   url: fileInfo.file.url_private,
//   headers: {
//     'Authorization': `Bearer ${process.env.BOT_TOKEN}`
//   },
//   responseType: 'arraybuffer'
// }).then(response => response.data);

//     const uploadFormData = new FormData();
//     uploadFormData.append('file', fileContent, {
//       filename: fileInfo.file.name || 'unknown',
//       contentType: fileInfo.file.mimetype || 'application/octet-stream'
//     });

//     Object.entries(formData).forEach(([key, value]) => {
//       if (value) {
//         if (Array.isArray(value)) {
//           value.forEach(item => uploadFormData.append(`${key}[]`, item));
//         } else {
//           uploadFormData.append(key, value);
//         }
//       }
//     });

//     const response = await axios.post(
//       `${process.env.QUESTIONNAIRE_BACKEND_URL}/api/v1/workflow`,
//       uploadFormData,
//       {
//         headers: {
//           Authorization: `Bearer ${accessToken}`,
//           ...uploadFormData.getHeaders()
//         },
//       }
//     );
   

//     if (response.status === 201) {
//       await client.chat.postMessage({
//         channel: (body as any).user.id,
      
//         text: ` File "${fileInfo.file.name}" successfully uploaded to QnA workflow. :white_check_mark:`,
//       });
//     } else {
//       throw new Error(response.data.errorMessage || 'Upload failed');
//     }
//   } catch (error) {
//     logger.error("Error uploading file to QnA workflow:", error);
    
//     const errorMessage = error instanceof Error ? error.message : 'Unknown error occurred';
    
//     await client.chat.postMessage({
//       channel: (body as any).user.id,
      
//       text: "Failed to upload file to QnA workflow. Please try again later.",
//     });
//   }
// });

// app.action("upload_to_knowledgebase", async ({ ack, body, client, logger }) => {
//   await ack();

//   const { fileIds } = JSON.parse((body as any).actions[0].value); 
//   console.log(fileIds, "fileIds");

//   if (!(body as any).channel?.id) {
//     logger.error("Channel not found in body");
//     return;
//   }

//   const loadingMessage = await client.chat.postMessage({
//     channel: (body as any).channel.id,
//     text: `Uploading files to knowledgebase... :hourglass_flowing_sand:`,
//   });

//   if (!loadingMessage.ts) {
//     logger.error("Failed to create loading message");
//     return;
//   }

//   const messageTs = loadingMessage.ts;

//   try {
//     const lookupResult = await client.users.info({
//       user: (body as any).user.id,
//     });

//     const email = lookupResult.user?.profile?.email;
//     if (!email) {
//       await client.chat.update({
//         channel: (body as any).channel.id,
//         ts: messageTs,
//         text: "Error: Unable to get user email. Please try again later.",
//       });
//       return;
//     }

//     let result = await getUserByEmail(email);

//     if (result.statusCode !== 200) {
//       await client.chat.update({
//         channel: (body as any).channel.id,
//         ts: messageTs,
//         text: "Error: Unable to authenticate user. Please try again later.",
//       });
//       return;
//     }

//     const user = result.data as any;
//     const accessToken = authJwtGenerator(
//       user.email,
//       user._id,
//       user.orgId,
//       user.fullName,
//       user.profileURL,
//       user.mobile,
//       user.slug
//     );

//     const uploadResults: string[] = [];
//     for (const fileId of fileIds) {
//       try {
//         const fileInfo = await client.files.info({ file: fileId });

//         if (!fileInfo.file?.url_private) {
//           uploadResults.push(`Failed to access file with ID "${fileId}".`);
//           continue;
//         }

// const fileContent = await axios({
//   method: 'get',
//   url: fileInfo.file.url_private,
//   headers: {
//     'Authorization': `Bearer ${process.env.BOT_TOKEN}`
//   },
//   responseType: 'arraybuffer'
// }).then(response => response.data);

//         const formData = new FormData();
//         formData.append("file", fileContent, {
//           filename: fileInfo.file.name || 'unknown',
//           contentType: fileInfo.file.mimetype || 'application/octet-stream',
//         });

//         const response = await axios.post(
//           `${process.env.QUESTIONNAIRE_BACKEND_URL}/api/v1/knowledgebase`,
//           formData,
//           {
//             headers: {
//               Authorization: `Bearer ${accessToken}`,
//               ...formData.getHeaders(),
//             },
//           }
//         );

//         if (response.status === 200) {
//           uploadResults.push(`File "${fileInfo.file.name}" uploaded successfully.`);
//         } else {
//           uploadResults.push(`Failed to upload file "${fileInfo.file.name}".`);
//         }
//       } catch (fileError) {
//         logger.error(`Error processing file with ID ${fileId}:`, fileError);
//         uploadResults.push(`Failed to upload file with ID "${fileId}".`);
//       }
//     }

   
//     const summaryMessage = uploadResults.join("\n");
//     await client.chat.update({
//       channel: (body as any).channel.id,
//       ts: messageTs,
//       text: `File upload completed:\n${summaryMessage}`,
//     });
//   } catch (error) {
//     logger.error("Error uploading files to knowledgebase:", error);

//     await client.chat.update({
//       channel: (body as any).channel.id,
//       ts: messageTs,
//       text: "Failed to upload files to knowledgebase. Please try again later. :x:",
//     });
//   }
// });

// app.action("search_button", async ({ ack, body, client, logger }) => {
//   await ack();
  
//   const { searchText, threadTs } = JSON.parse((body as any).actions[0].value);

//   if (!(body as any).channel?.id) {
//     logger.error("Channel not found in body");
//     return;
//   }

//   const loadingMessage = await client.chat.postMessage({
//     channel: (body as any).channel.id,
//     thread_ts: threadTs,
//     text: `Searching for "${searchText}"... :hourglass_flowing_sand:`,
//   });

//   if (!loadingMessage.ts) {
//     logger.error("Failed to create loading message");
//     return;
//   }

//   const messageTs = loadingMessage.ts;

//   await client.chat.update({
//     channel: (body as any).channel.id,
//     ts: messageTs,
//     blocks: [
//       {
//         type: "section",
//         text: {
//           type: "mrkdwn",
//           text: `Searching for "${searchText}"... :hourglass_flowing_sand:`,
//         },
//       },
//     ],
//     text: `Searching for "${searchText}"...`,
//   });

//   try {
//     const lookupResult = await client.users.info({
//       user: (body as any).user.id,
//     });
   
//     const email = lookupResult.user?.profile?.email;
//     if (!email) {
//       await client.chat.update({
//         channel: (body as any).channel.id,
//         ts: messageTs,
//         blocks: [
//           {
//             type: "section",
//             text: {
//               type: "mrkdwn",
//               text: "Error: Unable to get user email. Please try again later.",
//             },
//           },
//         ],
//         text: "Error: Unable to get user email.",
//       });
//       return;
//     }
    
//     let result = await getUserByEmail(email);
  
//     if (result.statusCode !== 200) {
//       await client.chat.update({
//         channel: (body as any).channel.id,
//         ts: messageTs,
//         blocks: [
//           {
//             type: "section",
//             text: {
//               type: "mrkdwn",
//               text: "Error: Unable to authenticate user. Please try again later.",
//             },
//           },
//         ],
//         text: "Error: Unable to authenticate user.",
//       });
//       return;
//     }
  
//     const user = result.data as any;
//     const userId = user._id;
//     const userSlug = user.slug;
//     const fullName = user.fullName;
//     const mobile = user.mobile;
//     const orgId = user.orgId;
//     const profileURL = user.profileURL;
  
//     const accessToken = authJwtGenerator(
//       user.email,
//       userId,
//       orgId,
//       fullName,
//       profileURL,
//       mobile,
//       userSlug
//     );
//     const response = await axios.post<SearchResponse>(
//       `${process.env.QUESTIONNAIRE_BACKEND_URL}/api/v1/knowledgebase/search?topK=20`,
//       { searchtext: searchText }, 
//       {
//         headers: {
//           Authorization: `Bearer ${accessToken}`,
//           "Content-Type": "application/json",
//         },
//       }
//     );

//     const blocks: any[] = [
//       {
//         type: "section",
//         text: {
//           type: "mrkdwn",
//           text: `*Search Results for "${searchText}"*`,
//         },
//       },
//       {
//         type: "divider",
//       },
//     ];

//     response.data.records.forEach((record, index) => {
//       const content = response.data[index] ? (response.data as any)[index].content : "No content preview available";
      
//       blocks.push({
//         type: "section",
//         text: {
//           type: "mrkdwn",
//           text: `*${record.name}*\n${content.substring(0, 150)}${content.length > 150 ? '...' : ''}`,
//         },
//       });

//       const categories = record.appSpecificRecordType.map(type => type.name).join(", ");
//       const departments = record.departments.map(dept => dept.name).join(", ");
      
//       blocks.push({
//         type: "context",
//         elements: [
//           {
//             type: "mrkdwn",
//             text: `*Categories:* ${categories} | *Departments:* ${departments}`,
//           },
//         ],
//       });

//       blocks.push({
//         type: "actions",
//         elements: [
//           {
//             type: "button",
//             text: {
//               type: "plain_text",
//               text: "View Details",
//               emoji: true,
//             },
//             value: JSON.stringify({
//               recordId: record._id,
//               recordName: record.name,
//               content: content,
//               metadata: {
//                 recordType: record.recordType,
//                 status: record.status,
//                 createdAt: record.createdAt,
//                 departments: record.departments,
//                 categories: record.appSpecificRecordType,
//                 fileInfo: response.data.fileRecords.find(f => f.recordId === record._id),
//               },
//             }),
//             action_id: `view_search_result_${index}`,
//           },
//         ],
//       });

//       blocks.push({
//         type: "divider",
//       });
//     });

//     await client.chat.update({
//       channel: (body as any).channel.id,
//       ts: messageTs,
//       blocks: blocks,
//       text: `Search Results for "${searchText}"`, 
//     });
//   } catch (error) {
//     logger.error("Error calling the API:", error);

//     await client.chat.update({
//       channel: (body as any).channel.id,
//       ts: messageTs,
//       text: "Something went wrong while fetching the results. Please try again later.",
//     });
//   }
// });

// app.action(/^view_search_result_\d+$/, async ({ ack, body, client }) => {
//   await ack();

//   try {
//     const recordData = JSON.parse((body as any).actions[0].value);
    
//     const modalBlocks: any[] = [
//       {
//         type: "section",
//         text: {
//           type: "mrkdwn",
//           text: "*Record Details*",
//         },
//       },
//       {
//         type: "divider",
//       },
//       {
//         type: "section",
//         text: {
//           type: "mrkdwn",
//           text: `*Name*\n${recordData.recordName}`,
//         },
//       },
//       {
//         type: "section",
//         text: {
//           type: "mrkdwn",
//           text: `*Record Type*\n${recordData.metadata.recordType}`,
//         },
//       },
//       {
//         type: "section",
//         text: {
//           type: "mrkdwn",
//           text: `*Status*\n${recordData.metadata.status}`,
//         },
//       },
//       {
//         type: "section",
//         text: {
//           type: "mrkdwn",
//           text: `*Content*\n${recordData.content}`,
//         },
//       },
//       {
//         type: "section",
//         text: {
//           type: "mrkdwn",
//           text: `*Departments*\n${recordData.metadata.departments.map((d: any) => d.name).join(", ")}`,
//         },
//       },
//       {
//         type: "section",
//         text: {
//           type: "mrkdwn",
//           text: `*Categories*\n${recordData.metadata.categories.map((c: any) => c.name).join(", ")}`,
//         },
//       },
//       {
//         type: "section",
//         text: {
//           type: "mrkdwn",
//           text: `*Created At*\n${new Date(recordData.metadata.createdAt).toLocaleString()}`,
//         },
//       }
//     ];

   
//     if (recordData.metadata.fileInfo) {
//       modalBlocks.push({
//         type: "section",
//         text: {
//           type: "mrkdwn",
//           text: `*File Information*\nName: ${recordData.metadata.fileInfo.fileName}\nExtension: ${recordData.metadata.fileInfo.extension}`,
//         },
//       });
//     }

   
//     modalBlocks.push({
//       type: "actions",
//       elements: [
//         {
//           type: "button",
//           text: {
//             type: "plain_text",
//             text: "View Record",
//             emoji: true,
//           },
//           url: `https://playground.intellysense.com/knowledge-base/records/${recordData.recordId}`,
//           action_id: "view_record",
//         },
//       ],
//     });

//     await client.views.open({
//       trigger_id: (body as any).trigger_id,
//       view: {
//         type: "modal",
//         title: {
//           type: "plain_text",
//           text: "Record Details",
//         },
//         blocks: modalBlocks,
//       },
//     });
//   } catch (error) {
//     console.error("Error opening record details modal:", error);
//   }
// });

// app.action("chat_button", async ({ ack, body, client, logger }) => {
//   await ack();

//   const { searchText, threadTs } = JSON.parse((body as any).actions[0].value);

//   if (!(body as any).channel?.id) {
//     logger.error("Channel not found in body");
//     return;
//   }

//   const loadingMessage = await client.chat.postMessage({
//     channel: (body as any).channel.id,
//     thread_ts: threadTs,
//     text: `Generating response... :hourglass_flowing_sand:`,
//   });

//   if (!loadingMessage.ts) {
//     logger.error("Failed to create loading message");
//     return;
//   }

//   const messageTs = loadingMessage.ts;

//   try {
//     const lookupResult = await client.users.info({
//       user: (body as any).user.id,
//     });
//     const email = lookupResult.user?.profile?.email;
//     if (!email) {
//       await client.chat.update({
//         channel: (body as any).channel.id,
//         ts: messageTs,
//         text: "Error: Unable to get user email. Please try again later.",
//       });
//       return;
//     }
    
//     const accessToken = authScopedJwtGenerator(email);
    
    
//     const response = await axios.post<ConversationData>(
//       `${process.env.QUESTIONNAIRE_BACKEND_URL}/api/v1/conversations/internal/create`,
//       {
//         query: searchText,
//         conversationSource: "sales",
//         chatMode: "quick"
//       },
//       {
//         headers: {
//           Authorization: `Bearer ${accessToken}`,
//           "Content-Type": "application/json",
//         },
//       }
//     );

//     const botResponse = response.data.conversation.messages.find(
//       (message) => message.messageType === "bot_response"
//     );

//     const conversationId = response.data.conversation._id;

//     await saveToDatabase({ threadId: threadTs, conversationId, email });

//     if (botResponse) {
      

//       if (botResponse.citations && botResponse.citations.length > 0) {
//         let citationUrls: CitationUrls = {};
//         botResponse.citations.forEach((citation) => {
//           let webUrl = citation.citationData.metadata.webUrl;
//           let chunkIndex = citation.citationData.chunkIndex;
//           if (!webUrl?.startsWith("https://")) {
//             webUrl = (process.env.FRONTEND_PUBLIC_URL || '') + (webUrl || '');
//           }
//           if (chunkIndex) {
//             citationUrls[chunkIndex] = webUrl || '';
//           }
//         });
//         botResponse.content = convertCitationsToHyperlinks(botResponse.content, citationUrls);
        

       
//     }
//     const blocks = [
//       {
//         type: "section",
//         text: {
//           type: "mrkdwn",
//           text: botResponse.content,
//         },
//       },
//     ];
//     await client.chat.update({
//       channel: (body as any).channel.id,
//       ts: messageTs,
//       blocks: blocks,
//       text: `ChatBot Response for "${searchText}"`,
//     });
   
//   }
//   else {
//       await client.chat.update({
//         channel: (body as any).channel.id,
//         ts: messageTs,
//         text: "Something went wrong! Please try again later.",
//       });
//     }
// }catch (error) {
//   logger.error("Error calling the Chat API:", error);
//   await client.chat.update({
//     channel: (body as any).channel.id,
//     ts: messageTs,
//     text: "Something went wrong! Please try again later.",
//   });
// }
// });

// app.action(/^view_citation_details_\d+$/, async ({ ack, body, client }) => {
//   await ack();

//   try {
//     const citationData = JSON.parse((body as any).actions[0].value);
    
//     const modalBlocks: any[] = [
//       {
//         type: "section",
//         text: {
//           type: "mrkdwn",
//           text: "*Record Details*",
//         },
//       },
//       {
//         type: "divider",
//       },
//       {
//         type: "section",
//         text: {
//           type: "mrkdwn",
//           text: `*Name*\n${citationData.recordName}`,
//         },
//       },
//       {
//         type: "section",
//         text: {
//           type: "mrkdwn",
//           text: `*Record Type*\n${citationData.metadata.recordType}`,
//         },
//       },
//       {
//         type: "section",
//         text: {
//           type: "mrkdwn",
//           text: `*Content*\n${citationData.content}`,
//         },
//       },
//       {
//         type: "section",
//         text: {
//           type: "mrkdwn",
//           text: `*Departments*\n${citationData.metadata.departments.join(", ")}`,
//         },
//       },
//       {
//         type: "section",
//         text: {
//           type: "mrkdwn",
//           text: `*Categories*\n${citationData.metadata.categories.map((cat: any) => 
//             `${cat.category}: ${cat.subcategories.join(", ")}`
//           ).join("\n")}`,
//         },
//       },
//       {
//         type: "section",
//         text: {
//           type: "mrkdwn",
//           text: `*Created At*\n${new Date(citationData.metadata.createdAt).toLocaleString()}`,
//         },
//       },
//       {
//         type: "actions",
//         elements: [
//           {
//             type: "button",
//             text: {
//               type: "plain_text",
//               text: "View Record",
//               emoji: true,
//             },
            
//             url: `https://playground.intellysense.com/knowledge-base/records/${citationData.recordId}`,
//             action_id: "view_record",
//           },
//         ],
//       },
//     ];

//     await client.views.open({
//       trigger_id: (body as any).trigger_id,
//       view: {
//         type: "modal",
//         title: {
//           type: "plain_text",
//           text: "Record Details",
//         },
//         blocks: modalBlocks,
//       },
//     });
//   } catch (error) {
//     console.error("Error opening citation details modal:", error);
//   }
// });

// Start the application
(async () => {
  await connect();
  await app.start(process.env.SLACK_BOT_PORT || 3020);
  console.log("Bolt app is running on 3020.");
})();