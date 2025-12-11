import { MCPTool } from "mcp-framework";
import { z } from "zod";

interface BooksInput {
  message: string;
}

class BooksTool extends MCPTool<BooksInput> {
  name = "list_books";
  description = "Best Books of the 21st Century";

  schema = {
    // message: {
    //   type: z.string(),
    //   description: "Message to process",
    // },
  };

  async execute() {
    return {
        books: Books
    };
  }
}

const Books = [
    {
        "Rank": 1,
        "Title": "My Brilliant Friend (Neapolitan Novels, No. 1)",
        "Author": "Elena Ferrante",
        "Page Count": 336,
        "Primary Genre": "Literary Fiction",
        "Price (USD)": 17.0,
        "Description": "This novel chronicles the intense and complex friendship and intellectual rivalry between two ambitious girls, Elena and Lila, growing up in a poor neighborhood in 1950s Naples."
    },
    {
        "Rank": 2,
        "Title": "The Warmth of Other Suns",
        "Author": "Isabel Wilkerson",
        "Page Count": 640,
        "Primary Genre": "Narrative Nonfiction",
        "Price (USD)": 21.0,
        "Description": "A masterful history of the Great Migration, detailing the decades-long exodus of six million Black Americans from the Jim Crow South to the North and West through three individual journeys."
    },
    {
        "Rank": 3,
        "Title": "Wolf Hall",
        "Author": "Hilary Mantel",
        "Page Count": 653,
        "Primary Genre": "Historical Fiction",
        "Price (USD)": 19.0,
        "Description": "A fictional biography of Thomas Cromwell, King Henry VIII's ruthless chief minister, chronicling his rise from humble beginnings to the pinnacle of power in Tudor England."
    },
    {
        "Rank": 4,
        "Title": "The Known World",
        "Author": "Edward P. Jones",
        "Page Count": 388,
        "Primary Genre": "Historical Fiction",
        "Price (USD)": 17.0,
        "Description": "Set in antebellum Virginia, this novel explores the moral complexities of slavery, focusing on a black man who is both a former slave and the owner of his own plantation and slaves."
    },
    {
        "Rank": 5,
        "Title": "The Corrections",
        "Author": "Jonathan Franzen",
        "Page Count": 568,
        "Primary Genre": "Literary Fiction",
        "Price (USD)": 19.0,
        "Description": "An epic family drama following the Midwestern Lambert parents and their three very different adult children as they navigate personal crises and an attempt at a final Christmas gathering."
    },
    {
        "Rank": 6,
        "Title": "2666",
        "Author": "Roberto Bolaño",
        "Page Count": 912,
        "Primary Genre": "Literary Fiction",
        "Price (USD)": 29.0,
        "Description": "A sprawling, complex masterpiece focused on the mystery of a reclusive German author and the unsolved, ongoing serial murders of women in a fictional Mexican border city."
    },
    {
        "Rank": 7,
        "Title": "Gilead",
        "Author": "Marilynne Robinson",
        "Page Count": 247,
        "Primary Genre": "Literary Fiction",
        "Price (USD)": 15.0,
        "Description": "Written as a long letter from an elderly, dying Congregationalist pastor in Iowa to his young son, reflecting on his life, faith, and family history."
    },
    {
        "Rank": 8,
        "Title": "A Visit from the Goon Squad",
        "Author": "Jennifer Egan",
        "Page Count": 340,
        "Primary Genre": "Literary Fiction / Linked Stories",
        "Price (USD)": 17.0,
        "Description": "A novel told in interconnected stories that follow a diverse set of characters linked to the music industry, exploring themes of time, decay, and redemption."
    },
    {
        "Rank": 9,
        "Title": "Sula",
        "Author": "Toni Morrison",
        "Page Count": 174,
        "Primary Genre": "Literary Fiction",
        "Price (USD)": 15.0,
        "Description": "The story of two African American women, Nel and Sula, and their lifelong friendship, which is tested by societal expectations and personal choices in a small Ohio town."
    },
    {
        "Rank": 10,
        "Title": "Behind the Beautiful Forevers",
        "Author": "Katherine Boo",
        "Page Count": 288,
        "Primary Genre": "Narrative Nonfiction",
        "Price (USD)": 17.0,
        "Description": "A vivid, ground-level portrait of life, death, and hope in a slum (undercity) next to a luxury hotel in Mumbai, India, revealing the high cost of global inequality."
    },
    {
        "Rank": 11,
        "Title": "The Underground Railroad",
        "Author": "Colson Whitehead",
        "Page Count": 306,
        "Primary Genre": "Historical Fiction / Fantasy",
        "Price (USD)": 18.0,
        "Description": "A novel that re-imagines the Underground Railroad as a literal railroad network, following a slave named Cora on her desperate journey to freedom from a Georgia plantation."
    },
    {
        "Rank": 12,
        "Title": "Fates and Furies",
        "Author": "Lauren Groff",
        "Page Count": 400,
        "Primary Genre": "Literary Fiction",
        "Price (USD)": 18.0,
        "Description": "The story of a marriage, told in two parts: first from the husband's perspective, Lotto, and then from the wife's, Mathilde, revealing the secrets and lies underpinning their relationship."
    },
    {
        "Rank": 13,
        "Title": "The Road",
        "Author": "Cormac McCarthy",
        "Page Count": 241,
        "Primary Genre": "Post-Apocalyptic Fiction",
        "Price (USD)": 16.0,
        "Description": "In a post-apocalyptic world, a man and his son walk south toward the coast, trying to maintain their humanity and evade cannibals."
    },
    {
        "Rank": 14,
        "Title": "Say Nothing",
        "Author": "Patrick Radden Keefe",
        "Page Count": 526,
        "Primary Genre": "Narrative Nonfiction / True Crime",
        "Price (USD)": 19.0,
        "Description": "A gripping investigation into the abduction and murder of Jean McConville by the IRA, unraveling the human cost, secrets, and silences of The Troubles in Northern Ireland."
    },
    {
        "Rank": 15,
        "Title": "The Sellout",
        "Author": "Paul Beatty",
        "Page Count": 289,
        "Primary Genre": "Satirical Fiction",
        "Price (USD)": 17.0,
        "Description": "A savage satire about an African-American man attempting to put his segregated, small Californian town back on the map by reinstituting slavery and segregation in his neighborhood."
    },
    {
        "Rank": 16,
        "Title": "Lincoln in the Bardo",
        "Author": "George Saunders",
        "Page Count": 368,
        "Primary Genre": "Historical Fiction / Magical Realism",
        "Price (USD)": 18.0,
        "Description": "Set in 1862, the novel imagines Abraham Lincoln visiting the crypt of his recently deceased son, Willie, where a chorus of garrulous ghosts resides in a state of limbo."
    },
    {
        "Rank": 17,
        "Title": "Erasure",
        "Author": "Percival Everett",
        "Page Count": 294,
        "Primary Genre": "Satirical Fiction / Literary",
        "Price (USD)": 17.0,
        "Description": "A frustrated, African-American novelist writes a satirical novel under a pseudonym, only to see it become a runaway bestseller that embodies the stereotypes he detests."
    },
    {
        "Rank": 18,
        "Title": "Evicted",
        "Author": "Matthew Desmond",
        "Page Count": 432,
        "Primary Genre": "General Nonfiction / Sociology",
        "Price (USD)": 18.0,
        "Description": "A deeply researched exposé showing how eviction is a cause, not just a consequence, of poverty, following the lives of eight families in Milwaukee."
    },
    {
        "Rank": 19,
        "Title": "The Road",
        "Author": "Cormac McCarthy",
        "Page Count": 241,
        "Primary Genre": "Post-Apocalyptic Fiction",
        "Price (USD)": 16.0,
        "Description": "In a post-apocalyptic world, a man and his son walk south toward the coast, trying to maintain their humanity and evade cannibals."
    },
    {
        "Rank": 20,
        "Title": "Behind the Beautiful Forevers",
        "Author": "Katherine Boo",
        "Page Count": 288,
        "Primary Genre": "Narrative Nonfiction",
        "Price (USD)": 17.0,
        "Description": "A vivid, ground-level portrait of life, death, and hope in a slum (undercity) next to a luxury hotel in Mumbai, India, revealing the high cost of global inequality."
    }
]

export default BooksTool;