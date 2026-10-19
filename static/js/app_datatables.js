/**
 * B ONE DataTables & Row-Click Quick View System
 * Rule: Clicking ANY table row automatically triggers Quick View drawer or detail navigation.
 */

class BytebootDataTable {
    constructor(tableElem, options = {}) {
        this.table = tableElem;
        this.options = Object.assign({
            pageSize: 10,
            searchable: true,
            sortable: true,
            filterable: true
        }, options);

        this.currentPage = 1;
        this.searchQuery = "";
        this.sortColumnIndex = -1;
        this.sortDirection = "asc"; // 'asc' or 'desc'

        this.init();
    }

    init() {
        if (!this.table) return;

        this.tbody = this.table.querySelector("tbody");
        if (!this.tbody) return;

        this.originalRows = Array.from(this.tbody.querySelectorAll("tr")).filter(
            r => !r.classList.contains("empty-row")
        );

        this.filteredRows = [...this.originalRows];

        // Attach Row-Click Quick View Event Rule
        this.setupRowClickEvents();

        this.renderControls();
        this.setupSorting();
        this.updateTable();
    }

    setupRowClickEvents() {
        this.originalRows.forEach(row => {
            row.classList.add("cursor-pointer", "transition");

            row.addEventListener("click", (e) => {
                // If user clicked directly on an interactive button/link/input, let it behave normally
                if (e.target.closest("a, button, input, select, textarea")) {
                    return;
                }

                // Check if row has an HTMX trigger element or link
                const htmxBtn = row.querySelector("[hx-get]");
                if (htmxBtn) {
                    htmxBtn.click();
                    return;
                }

                const link = row.querySelector("a[href]");
                if (link) {
                    link.click();
                }
            });
        });
    }

    renderControls() {
        const wrapper = document.createElement("div");
        wrapper.className = "datatable-controls-wrap flex flex-col md:flex-row md:items-center justify-between gap-3 mb-4 text-xs text-slate-600 dark:text-slate-300";

        // Left side: Length menu
        const lengthDiv = document.createElement("div");
        lengthDiv.className = "flex items-center gap-2";
        lengthDiv.innerHTML = `
            <span>Show</span>
            <select class="dt-length-select bg-white dark:bg-[#162032] border border-slate-300 dark:border-white/10 text-slate-800 dark:text-slate-200 text-xs rounded-lg px-2.5 py-1.5 outline-none focus:border-[#1E5BFF]">
                <option value="5">5</option>
                <option value="10" selected>10</option>
                <option value="25">25</option>
                <option value="50">50</option>
            </select>
            <span>entries</span>
        `;
        const select = lengthDiv.querySelector(".dt-length-select");
        select.addEventListener("change", (e) => {
            this.options.pageSize = parseInt(e.target.value, 10);
            this.currentPage = 1;
            this.updateTable();
        });

        // Right side: Search Box
        const searchDiv = document.createElement("div");
        searchDiv.className = "flex items-center gap-2 relative";
        searchDiv.innerHTML = `
            <span class="text-slate-500 dark:text-slate-400">Search:</span>
            <input type="text" placeholder="Filter table rows..." class="dt-search-input bg-white dark:bg-[#162032] border border-slate-300 dark:border-white/10 rounded-lg px-3 py-1.5 text-xs text-slate-800 dark:text-slate-100 outline-none focus:border-[#1E5BFF] focus:ring-1 focus:ring-[#1E5BFF] w-64 shadow-sm">
        `;
        const input = searchDiv.querySelector(".dt-search-input");
        input.addEventListener("input", (e) => {
            this.searchQuery = e.target.value.toLowerCase().trim();
            this.currentPage = 1;
            this.filterRows();
        });

        wrapper.appendChild(lengthDiv);
        wrapper.appendChild(searchDiv);

        this.table.parentNode.insertBefore(wrapper, this.table);

        // Footer info & Pagination controls
        this.footer = document.createElement("div");
        this.footer.className = "datatable-footer flex flex-col md:flex-row md:items-center justify-between gap-3 mt-4 text-xs text-slate-500 dark:text-slate-400 border-t border-slate-200 dark:border-white/10 pt-3";
        this.table.parentNode.appendChild(this.footer);
    }

    setupSorting() {
        const headers = this.table.querySelectorAll("thead th");
        headers.forEach((th, idx) => {
            if (th.dataset.sortable !== "false") {
                th.classList.add("cursor-pointer", "select-none", "hover:text-[#1E5BFF]", "transition");
                th.setAttribute("title", "Click to sort column");
                th.addEventListener("click", () => this.sort(idx));
            }
        });
    }

    filterRows() {
        if (!this.searchQuery) {
            this.filteredRows = [...this.originalRows];
        } else {
            this.filteredRows = this.originalRows.filter(row => {
                const text = row.textContent.toLowerCase();
                return text.includes(this.searchQuery);
            });
        }
        this.updateTable();
    }

    sort(columnIndex) {
        if (this.sortColumnIndex === columnIndex) {
            this.sortDirection = this.sortDirection === "asc" ? "desc" : "asc";
        } else {
            this.sortColumnIndex = columnIndex;
            this.sortDirection = "asc";
        }

        const headers = this.table.querySelectorAll("thead th");
        headers.forEach((th, idx) => {
            th.querySelectorAll(".sort-icon").forEach(el => el.remove());
            if (idx === columnIndex) {
                const icon = document.createElement("span");
                icon.className = "sort-icon ml-1 text-[#1E5BFF] font-bold";
                icon.textContent = this.sortDirection === "asc" ? " ▲" : " ▼";
                th.appendChild(icon);
            }
        });

        this.filteredRows.sort((a, b) => {
            const cellA = a.children[columnIndex] ? a.children[columnIndex].textContent.trim() : "";
            const cellB = b.children[columnIndex] ? b.children[columnIndex].textContent.trim() : "";
            
            const numA = parseFloat(cellA.replace(/[^0-9.-]+/g, ""));
            const numB = parseFloat(cellB.replace(/[^0-9.-]+/g, ""));

            let comparison = 0;
            if (!isNaN(numA) && !isNaN(numB)) {
                comparison = numA - numB;
            } else {
                comparison = cellA.localeCompare(cellB);
            }

            return this.sortDirection === "asc" ? comparison : -comparison;
        });

        this.updateTable();
    }

    updateTable() {
        const total = this.filteredRows.length;
        const totalPages = Math.ceil(total / this.options.pageSize) || 1;
        if (this.currentPage > totalPages) this.currentPage = totalPages;

        const startIdx = (this.currentPage - 1) * this.options.pageSize;
        const endIdx = Math.min(startIdx + this.options.pageSize, total);

        this.originalRows.forEach(r => (r.style.display = "none"));

        if (total === 0) {
            this.tbody.innerHTML = `<tr class="empty-row"><td colspan="100" class="text-center text-slate-400 py-6">No matching records found.</td></tr>`;
        } else {
            const emptyRow = this.tbody.querySelector(".empty-row");
            if (emptyRow) emptyRow.remove();

            for (let i = startIdx; i < endIdx; i++) {
                if (this.filteredRows[i]) {
                    this.filteredRows[i].style.display = "";
                    this.tbody.appendChild(this.filteredRows[i]);
                }
            }
        }

        const showingStart = total === 0 ? 0 : startIdx + 1;
        this.footer.innerHTML = `
            <div>Showing <strong>${showingStart}</strong> to <strong>${endIdx}</strong> of <strong>${total}</strong> entries</div>
            <div class="flex items-center gap-1.5">
                <button class="dt-prev bg-white dark:bg-[#162032] border border-slate-300 dark:border-white/10 hover:bg-slate-50 dark:hover:bg-[#1e2d44] text-slate-700 dark:text-slate-200 px-3 py-1.5 rounded-lg text-xs font-medium disabled:opacity-40 disabled:cursor-not-allowed shadow-sm transition" ${this.currentPage === 1 ? "disabled" : ""}>Previous</button>
                <span class="px-2 font-bold text-[#1E5BFF]">Page ${this.currentPage} of ${totalPages}</span>
                <button class="dt-next bg-white dark:bg-[#162032] border border-slate-300 dark:border-white/10 hover:bg-slate-50 dark:hover:bg-[#1e2d44] text-slate-700 dark:text-slate-200 px-3 py-1.5 rounded-lg text-xs font-medium disabled:opacity-40 disabled:cursor-not-allowed shadow-sm transition" ${this.currentPage >= totalPages ? "disabled" : ""}>Next</button>
            </div>
        `;

        const prevBtn = this.footer.querySelector(".dt-prev");
        const nextBtn = this.footer.querySelector(".dt-next");

        if (prevBtn) prevBtn.addEventListener("click", () => {
            if (this.currentPage > 1) {
                this.currentPage--;
                this.updateTable();
            }
        });

        if (nextBtn) nextBtn.addEventListener("click", () => {
            if (this.currentPage < totalPages) {
                this.currentPage++;
                this.updateTable();
            }
        });
    }
}

// Auto-initialize all data tables on DOM ready
document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("table.app-table").forEach(table => {
        new BytebootDataTable(table);
    });
});
