# AI Agent Builder System

A comprehensive, enterprise-grade AI agent builder system built with React, TypeScript, and Material-UI. This system allows users to create, manage, and interact with AI agents using templates or from scratch.

## 🚀 Features

### Core Functionality
- **Agent Creation**: Multi-step wizard for building AI agents
- **Template System**: Pre-built agent templates for quick deployment
- **Agent Management**: Full CRUD operations for agents
- **Real-time Chat**: Interactive chat interface with streaming responses
- **Advanced Configuration**: Tools, models, integrations, and knowledge base setup

### User Experience
- **Modern UI**: High-end SaaS platform design with Material-UI
- **Responsive Design**: Works seamlessly on all device sizes
- **Real-time Updates**: Live streaming and status updates
- **Intuitive Workflow**: Step-by-step agent creation process
- **Smart Validation**: Form validation with helpful error messages

### Technical Features
- **TypeScript**: Full type safety and IntelliSense support
- **Modular Architecture**: Reusable components and utilities
- **API Integration**: Comprehensive service layer for backend communication
- **State Management**: Efficient React state handling
- **Error Handling**: Robust error handling and user feedback

## 🏗️ Architecture

### Component Structure
```
src/sections/qna/agents/
├── components/                 # Reusable UI components
│   ├── agent-builder.tsx      # Main builder wizard
│   ├── agent-builder-step1.tsx # Basic information
│   ├── agent-builder-step2.tsx # Configuration
│   ├── agent-builder-step3.tsx # Advanced settings
│   ├── agent-builder-step4.tsx # Review & create
│   └── template-selector.tsx  # Template selection
├── services/                   # API service layer
│   └── agent-api-service.ts   # HTTP client and API calls
├── utils/                      # Utility functions
│   └── agent-utils.ts         # Validation, formatting, etc.
├── agents-management.tsx       # Main management interface
├── agent-chat.tsx             # Chat interface
├── index.ts                   # Public exports
└── README.md                  # This documentation
```

### Data Flow
1. **User Input** → Form validation → API calls
2. **API Response** → State updates → UI re-render
3. **Real-time Events** → Streaming updates → Live UI updates

## 🎯 Usage

### Basic Implementation

```tsx
import { AgentsManagement } from 'src/sections/qna/agents';

function MyPage() {
  return (
    <div>
      <h1>AI Agents</h1>
      <AgentsManagement />
    </div>
  );
}
```

### Agent Builder

```tsx
import { AgentBuilder } from 'src/sections/qna/agents';

function MyComponent() {
  const [showBuilder, setShowBuilder] = useState(false);

  const handleAgentCreated = (agentData) => {
    console.log('New agent:', agentData);
    // Handle success
  };

  return (
    <AgentBuilder
      open={showBuilder}
      onClose={() => setShowBuilder(false)}
      onSuccess={handleAgentCreated}
    />
  );
}
```

### Agent Chat

```tsx
import { AgentChat } from 'src/sections/qna/agents';

function ChatComponent({ agent }) {
  const [showChat, setShowChat] = useState(false);

  return (
    <AgentChat
      open={showChat}
      onClose={() => setShowChat(false)}
      agent={agent}
    />
  );
}
```

## 🔧 Configuration

### Environment Variables
```env
# API Configuration
REACT_APP_API_BASE_URL=/api/v1
REACT_APP_AGENT_API_BASE=/api/v1/agent
```

### API Endpoints
The system expects the following backend endpoints:

- `POST /api/v1/agent/template` - Create template
- `GET /api/v1/agent/template` - List templates
- `GET /api/v1/agent/template/:id` - Get template
- `PUT /api/v1/agent/template/:id` - Update template
- `DELETE /api/v1/agent/template/:id` - Delete template
- `POST /api/v1/agent/create` - Create agent
- `GET /api/v1/agent` - List agents
- `GET /api/v1/agent/:key` - Get agent
- `PUT /api/v1/agent/:key` - Update agent
- `DELETE /api/v1/agent/:key` - Delete agent
- `POST /api/v1/agent/:key/chat` - Start chat
- `POST /api/v1/agent/:key/chat/stream` - Stream chat

## 🎨 Customization

### Styling
The system uses Material-UI theming. Customize by overriding theme values:

```tsx
import { createTheme } from '@mui/material/styles';

const theme = createTheme({
  palette: {
    primary: {
      main: '#your-color',
    },
  },
  components: {
    MuiCard: {
      styleOverrides: {
        root: {
          borderRadius: 16,
        },
      },
    },
  },
});
```

### Component Props
All components accept standard Material-UI props and can be customized:

```tsx
<AgentBuilder
  open={true}
  onClose={() => {}}
  onSuccess={() => {}}
  selectedTemplate={template}
  // Additional props for customization
  maxWidth="xl"
  fullWidth
/>
```

## 📱 Responsive Design

The system is fully responsive and works on:
- **Desktop**: Full-featured interface with sidebars
- **Tablet**: Optimized layout for medium screens
- **Mobile**: Touch-friendly mobile interface

## 🔒 Security

- **Authentication**: JWT token-based authentication
- **Authorization**: Role-based access control
- **Input Validation**: Client and server-side validation
- **XSS Protection**: Sanitized user inputs
- **CSRF Protection**: Built-in CSRF protection

## 🚀 Performance

- **Lazy Loading**: Components load on demand
- **Memoization**: React.memo for expensive components
- **Virtual Scrolling**: For large lists (future enhancement)
- **Code Splitting**: Dynamic imports for better performance
- **Bundle Optimization**: Tree shaking and minification

## 🧪 Testing

### Unit Tests
```bash
npm test -- --testPathPattern=agents
```

### Integration Tests
```bash
npm run test:integration -- --testPathPattern=agents
```

### E2E Tests
```bash
npm run test:e2e -- --spec="agents/**/*.spec.js"
```

## 📚 API Reference

### Types

```typescript
interface Agent {
  _key: string;
  name: string;
  description: string;
  startMessage: string;
  systemPrompt: string;
  tools: string[];
  models: string[];
  apps: string[];
  kb: string[];
  vectorDBs: Array<{ id: string; name: string }>;
  tags: string[];
  isActive: boolean;
  agentKey: string;
  // ... more properties
}

interface AgentTemplate {
  _key: string;
  name: string;
  description: string;
  // ... similar to Agent
}
```

### Services

```typescript
class AgentApiService {
  static async createAgent(data: CreateAgentRequest): Promise<Agent>;
  static async listAgents(): Promise<Agent[]>;
  static async getAgent(agentKey: string): Promise<Agent>;
  static async updateAgent(agentKey: string, data: UpdateAgentRequest): Promise<Agent>;
  static async deleteAgent(agentKey: string): Promise<void>;
  static async streamAgentChat(agentKey: string, data: AgentChatRequest, onChunk, onComplete, onError): Promise<void>;
  // ... more methods
}
```

## 🔄 State Management

The system uses React's built-in state management with hooks:

- **useState**: Local component state
- **useEffect**: Side effects and API calls
- **useCallback**: Memoized functions
- **useMemo**: Computed values
- **useRef**: DOM references and mutable values

## 🌟 Best Practices

### Code Organization
- **Single Responsibility**: Each component has one clear purpose
- **Composition**: Components are composed from smaller pieces
- **Props Interface**: Clear TypeScript interfaces for all props
- **Error Boundaries**: Graceful error handling
- **Loading States**: Proper loading and error states

### Performance
- **Memoization**: Use React.memo for expensive components
- **Lazy Loading**: Load components only when needed
- **Debouncing**: Debounce user input for better performance
- **Virtualization**: For large lists (future enhancement)

### Accessibility
- **ARIA Labels**: Proper accessibility attributes
- **Keyboard Navigation**: Full keyboard support
- **Screen Readers**: Compatible with screen readers
- **Focus Management**: Proper focus handling
- **Color Contrast**: WCAG compliant color schemes

## 🚧 Future Enhancements

### Planned Features
- **Agent Analytics**: Performance metrics and insights
- **A/B Testing**: Test different agent configurations
- **Multi-language Support**: Internationalization
- **Advanced Templates**: More sophisticated template system
- **Agent Marketplace**: Share and discover agents
- **Integration Hub**: More third-party integrations
- **Advanced Analytics**: Detailed conversation analytics
- **Agent Training**: Continuous learning capabilities

### Technical Improvements
- **WebSocket Support**: Real-time bidirectional communication
- **Offline Support**: Service worker for offline functionality
- **PWA Features**: Progressive web app capabilities
- **Micro-frontends**: Modular architecture for large teams
- **GraphQL**: More efficient data fetching
- **Real-time Collaboration**: Multi-user editing

## 🤝 Contributing

### Development Setup
1. Clone the repository
2. Install dependencies: `npm install`
3. Start development server: `npm start`
4. Run tests: `npm test`
5. Build for production: `npm run build`

### Code Style
- **ESLint**: Follow project ESLint rules
- **Prettier**: Use Prettier for code formatting
- **TypeScript**: Strict TypeScript configuration
- **Component Structure**: Follow established patterns
- **Documentation**: Update documentation for new features

### Pull Request Process
1. Create feature branch
2. Implement changes with tests
3. Update documentation
4. Submit pull request
5. Code review and approval
6. Merge to main branch

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🆘 Support

### Getting Help
- **Documentation**: Check this README first
- **Issues**: Create GitHub issues for bugs
- **Discussions**: Use GitHub discussions for questions
- **Email**: Contact the development team

### Common Issues
- **API Errors**: Check network tab and API endpoints
- **Styling Issues**: Verify Material-UI theme configuration
- **Performance**: Use React DevTools for profiling
- **Build Errors**: Check TypeScript compilation

## 🔗 Related Links

- [Material-UI Documentation](https://mui.com/)
- [React Documentation](https://reactjs.org/)
- [TypeScript Handbook](https://www.typescriptlang.org/)
- [Project Repository](https://github.com/your-org/your-repo)
- [API Documentation](https://your-api-docs.com/)

---

**Built with ❤️ by the Development Team**
