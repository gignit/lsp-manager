PREFIX := $(HOME)/.local
BIN_DIR := $(PREFIX)/bin
INSTALL_DIR := $(PREFIX)/share/lsp-manager

# Files/dirs never shipped to the install location.
RSYNC_EXCLUDE := \
	--exclude='.git' \
	--exclude='.gitignore' \
	--exclude='.DS_Store' \
	--exclude='examples' \
	--exclude='__pycache__' \
	--exclude='*.pyc' \
	--exclude='*.pyo' \
	--exclude='*.egg-info' \
	--exclude='.eggs' \
	--exclude='.venv' \
	--exclude='venv' \
	--exclude='.env' \
	--exclude='.mypy_cache' \
	--exclude='.pytest_cache' \
	--exclude='.ruff_cache' \
	--exclude='htmlcov' \
	--exclude='.coverage' \
	--exclude='dist' \
	--exclude='build' \
	--exclude='node_modules' \
	--exclude='.npm' \
	--exclude='.turbo' \
	--exclude='.next' \
	--exclude='.nuxt' \
	--exclude='*.log' \
	--exclude='.idea' \
	--exclude='.vscode' \
	--exclude='*.swp' \
	--exclude='*.swo' \
	--exclude='*~'

# NOTE: install only ever copies -- it never deletes from INSTALL_DIR.
# Other tools may keep state there (and users may add their own server
# definitions), so this Makefile is not the authority over that
# directory. Cleanup of artifacts that *lsp-manager itself* created in
# older versions is handled surgically by `lsp-manager doctor --fix`.

.PHONY: install uninstall

install:
	@echo "> Installing lsp-manager to $(INSTALL_DIR)"
	@echo ""
	@mkdir -p $(BIN_DIR) $(INSTALL_DIR)
	@log=$$(mktemp); \
	rsync -rnc --itemize-changes $(RSYNC_EXCLUDE) ./ $(INSTALL_DIR)/ > $$log 2>&1; \
	rsync -rc $(RSYNC_EXCLUDE) ./ $(INSTALL_DIR)/; \
	rsync -r --list-only $(RSYNC_EXCLUDE) ./ /dev/null 2>&1 \
		| awk '!/^d/ {print $$NF}' \
		| while read -r file; do \
			if grep -q "^>f+++.*$$file$$" $$log 2>/dev/null; then \
				echo "  [copied]  $$file"; \
			elif grep -q "^>f.*$$file$$" $$log 2>/dev/null; then \
				echo "  [updated] $$file"; \
			else \
				echo "  [current] $$file"; \
			fi; \
		done; \
	rm -f $$log
	@chmod +x $(INSTALL_DIR)/lsp-manager
	@ln -sf $(INSTALL_DIR)/lsp-manager $(BIN_DIR)/lsp-manager
	@echo ""
	@echo "  [ok] lsp-manager -> $(BIN_DIR)/lsp-manager"
	@case ":$${PATH}:" in \
		*":$(BIN_DIR):"*) ;; \
		*) \
			echo ""; \
			echo "  NOTE: $(BIN_DIR) is not on your PATH."; \
			echo "        Add this to your shell rc (bash/zsh):"; \
			echo "          export PATH=\"\$$HOME/.local/bin:\$$PATH\""; \
			echo "        Or for fish:"; \
			echo "          fish_add_path \$$HOME/.local/bin"; \
			;; \
	esac
	@echo ""

uninstall:
	@echo "  NOTE: this removes $(INSTALL_DIR), including the local plugin"
	@echo "        marketplace. Any '@lsp-manager' plugins still enabled in"
	@echo "        Claude Code will stop working. Remove them first with:"
	@echo "          claude plugin uninstall <name>@lsp-manager"
	@rm -f $(BIN_DIR)/lsp-manager
	@rm -rf $(INSTALL_DIR)
	@echo "  [ok] lsp-manager uninstalled"
