# Agent & UI Issues Breakdown

## Mall Manager (MM)

### Agent Fixes

  ------------------------------------------------------------------------
  Priority              Issue            Required Fix
  --------------------- ---------------- ---------------------------------
  🔴 High               Time field is    Along with the date, populate a
                        not being asked  default timestamp in the format
                                         **DD/MM/YYYY hh:mm**. If the user
                                         explicitly provides a different
                                         time, update the field
                                         accordingly. Do **not** default
                                         to 9:00 AM without user
                                         confirmation.

  🔴 High               Notes update     When the Notes field has already
                        intent confusion been collected, subsequent user
                                         requests such as *"Change
                                         inspection conducted by to
                                         Operations"* should update the
                                         intended field instead of
                                         appending the text to Notes.
                                         Improve field update intent
                                         classification.

  🔴 High               Agent sometimes  Ensure Description is always
                        skips            collected before completion and
                        Description or   clearly distinguish Description
                        mistakes Notes   from Additional Notes during
                        as Description   extraction and updates.

  🔴 High               Cancel button    When the UI Cancel button is
                        flow             clicked, the agent should
                                         recognize it as a **cancel SR**
                                         intent instead of entering the
                                         field update loop. Ask for
                                         confirmation before cancelling,
                                         then complete the cancellation
                                         flow and synchronize correctly
                                         with the UI.

  🔴 High               Comments field   Agent should explicitly prompt
                        is never         the user for Comments as part of
                        requested        the required workflow.

  🟡 Medium             Document upload  Document upload should be
                        order            requested **only at the end** of
                                         the MM workflow. (Upload is
                                         optional for MM.) Agent should
                                         follow this sequence.
  ------------------------------------------------------------------------

### UI Validation / UI Fixes

  --------------------------------------------------------------------------
  Issue         Validation / Expected Behaviour
  ------------- ------------------------------------------------------------
  Unit Code is  Validate that the agent emits the Unit Code correctly. If
  not populated present in the agent payload, this is a UI binding issue.
  when SR is    
  created       
  through AI    

  Title is not  Validate that the agent emits the Title correctly. If yes,
  populated     the UI should populate the Title field.
  (only         
  Description   
  appears)      

  Additional    Validate that the agent emits the Notes field correctly and
  Notes entered that the UI updates the form.
  in chat are   
  not reflected 
  in the form   

  Cancel button After successful cancellation, ensure the UI closes the SR
  behaviour     instead of redisplaying the form. Agent and UI should remain
                synchronized.
  --------------------------------------------------------------------------

------------------------------------------------------------------------

## Floor Manager (FM)

### UI Validation / UI Fixes

  ---------------------------------------------------------------------------
  Issue          Validation / Expected Behaviour
  -------------- ------------------------------------------------------------
  Update button  Validate that the agent state transitions correctly. Once
  remains after  submission is confirmed, the Update button should disappear.
  document       
  upload and     
  auto-refresh   

  ---------------------------------------------------------------------------

------------------------------------------------------------------------

## RDD Project Manager (RDDPM)

### Agent Fixes

  ------------------------------------------------------------------------
  Priority              Issue            Required Fix
  --------------------- ---------------- ---------------------------------
  🔴 High               Workflow order   Agent should strictly follow the
                        is incorrect     workflow: **Guidelines → Document
                                         Type Selection → Approve /
                                         Approve with Comments → Actual
                                         Handover Date**.

  ------------------------------------------------------------------------

### UI Validation / UI Fixes

  -----------------------------------------------------------------------
  Issue      Validation / Expected Behaviour
  ---------- ------------------------------------------------------------
  Confirm    Validate that the agent state advances correctly so the UI
  button not displays the Confirm button.
  shown      
  after      
  document   
  upload     

  Update     Validate state synchronization so the Update button is
  button     removed after submission.
  remains    
  after      
  Confirm    
  Submit     

  Final      Validate that the agent transitions to the final approval
  Approve    state correctly.
  screen     
  state      

  Update and Validate that once Final Approve is completed, both buttons
  Final      are removed and the UI reflects the completed state.
  Approve    
  buttons    
  remain     
  even after 
  Final      
  Approve    
  -----------------------------------------------------------------------

------------------------------------------------------------------------

# High-Priority Agent Fixes

1.  Cancel button workflow
2.  Inspection date & time handling
3.  Notes field update intent
4.  Comments collection
5.  Description vs Notes extraction
6.  MM document upload ordering
7.  RDDPM workflow sequencing

# High-Priority UI Validation

1.  Unit Code population
2.  Title population
3.  Notes population
4.  MM Cancel flow synchronization
5.  FM Update button removal
6.  RDDPM Confirm button state
7.  RDDPM Update button removal
8.  RDDPM Final Approve state synchronization
